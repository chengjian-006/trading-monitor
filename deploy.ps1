# -Force: 跳过"工作区不干净"的确认(明知故犯时才用, 例如就是要带着未提交的改动上线验证)
param([switch]$Force)

$ErrorActionPreference = "Stop"

# 交互运行时等回车再退出; 非交互(自动化/被工具调用)时 Read-Host 会抛错, 用 try/catch 吞掉,
# 避免末尾暂停在 NonInteractive 下抛 PSInvalidOperationException 把整个脚本判成 exit 1 假失败。
function Pause-Exit {
    try { [void](Read-Host 'Press Enter to exit') } catch { }
}

# 跑原生 exe(ssh/scp/tar/npm)专用: 只按退出码判成败, 不让 stderr 参与。
# 起因: PS 5.1 在 stderr 被捕获/重定向时, 会把原生命令的每行 stderr 包成 NativeCommandError
# 记录; 叠加脚本顶部的 $ErrorActionPreference="Stop", 一行无害警告就能终止整个部署。
# 实际中招: tar 解包打印的 "Ignoring unknown extended header keyword 'SCHILY.fflags'" 让脚本
# 停在 [5/7], 【第 6 步重启服务根本没跑到】, 而前面各步都打了 Done, 看着像部署成功了。
# 注: 交互式终端里 stderr 不会被包成 ErrorRecord, 所以手敲跑通常没事 —— 但被工具/CI 调用时必炸,
# 且"无害警告有权终止部署"这件事本身就不该成立。
# 刻意【不写 param 块】: 带 [Parameter()] 的 param 会把函数变成高级函数, 于是 `-i`(ssh/scp 的
# 私钥参数)会被当成 PowerShell 通用参数去匹配 -InformationAction/-InformationVariable 而报
# "parameter name 'i' is ambiguous"。无 param 块时所有实参(含 -i 这种)原样进 $args, 不做绑定。
function Invoke-Native {
    $exe = $args[0]
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $exe @rest } finally { $ErrorActionPreference = $prev }
}

$SERVER = "124.71.75.5"
$USER = "root"
$REMOTE_DIR = "/opt/trading-monitor"
$KEY = "D:\财务管理\trading-keypair.pem"
$PROJECT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$ARCHIVE = "trading-deploy.tar.gz"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Deploy to Prod" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $KEY)) {
    Write-Host "Key not found: $KEY" -ForegroundColor Red
    Pause-Exit
    exit 1
}

# ── 预检 ────────────────────────────────────────────────────────────────
# 核心事实: 打包用的是 `tar ... .`,【打的是工作区, 不是提交状态】, 全程不查 git。由此有两个坑,
# 都在多会话并行改同一仓库时会踩(2026-07-20 两个都真踩到了):
#
#   坑一 · 脏工作区: 任何没提交完的半成品, 只要文件在盘上就会原样上生产; 前端更狠, 会先被
#     build 进 dist 一起传。那天把另一个会话正在改的 extension/ 半成品打包传了上去(不对外,
#     没造成影响); 换成 frontend/ 或 backend/ 就是半成品直接进生产。
#     规矩是 commit → push → deploy, 所以部署时工作区本就该干净; 脏就说明有人还在改。
#
#   坑二 · 树落后于 origin/main: 因为部署是"解压覆盖", 从落后的树部署会把别人已经上线的改动
#     【直接冲回旧版本】, 而且 git 层面毫无痕迹 —— 你根本没动那些文件。共用一个工作目录时这
#     反而不会发生(别人的提交天然在你树里), 一旦按会话拆 worktree 就必踩。
#     所以工作流定成: worktree 里随便开工, 合回 main、push 完, 再从 main 部署。
#     这道闸就是这条工作流的执行者 —— 只允许从 main 且与 origin/main 同步的树部署。
#
# 非交互(工具/CI)下 Read-Host 拿不到输入 → 按 N 处理 → 中止, 这是安全的默认。要强上加 -Force。
Push-Location $PROJECT_DIR
$issues = @()

# 坑一: 工作区脏
$dirty = Invoke-Native git status --porcelain
if ($dirty) {
    $issues += "工作区不干净, 以下改动会被【原样打包上生产】:"
    $dirty | ForEach-Object { $issues += "      $_" }
    $issues += "    → 如果这里面有别的会话没改完的东西, 现在停下来。先 commit 或 git stash。"
}

# 坑二之一: 不在 main 上
$branch = (Invoke-Native git rev-parse --abbrev-ref HEAD | Select-Object -First 1)
if ($branch -ne 'main') {
    $issues += "当前在分支 [$branch], 不是 main。"
    $issues += "    → 部署只从 main 出发。先把这个分支合回 main 再部署, 否则线上会变成半条支线。"
}

# 坑二之二: 与 origin/main 不同步。fetch 失败就当"没验成"处理 —— 没验成 != 验过了。
Invoke-Native git fetch origin main --quiet
if ($LASTEXITCODE -ne 0) {
    $issues += "git fetch origin main 失败(exit $LASTEXITCODE), 无法确认本地是否落后于远端。"
    $issues += "    → 网络不通就先修网络; 带着不确定部署可能把别人的改动冲掉。"
} else {
    $counts = (Invoke-Native git rev-list --left-right --count origin/main...HEAD | Select-Object -First 1)
    $behind, $ahead = ($counts -split '\s+') | Where-Object { $_ -ne '' }
    if ([int]$behind -gt 0) {
        $issues += "本地落后 origin/main $behind 个提交(领先 $ahead 个)。"
        $issues += "    → 部署是解压覆盖, 从落后的树部署会把别人已上线的改动冲回旧版本。先 git pull。"
    } elseif ([int]$ahead -gt 0) {
        # 领先不危险(线上会比 origin 新), 但破坏"线上 == origin/main"这个前提, 出事不好回溯
        Write-Host "提示: 本地领先 origin/main $ahead 个提交, 建议先 git push 再部署。" -ForegroundColor DarkYellow
    }
}
Pop-Location

if ($issues.Count -gt 0) {
    Write-Host ""
    Write-Host "部署预检未通过:" -ForegroundColor Yellow
    $issues | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkYellow }
    Write-Host ""
    if ($Force) {
        Write-Host "  指定了 -Force, 跳过确认继续。" -ForegroundColor DarkYellow
    } else {
        $ans = ''
        try { $ans = Read-Host '仍要继续部署? (y/N)' } catch { }
        if ($ans -notmatch '^[yY]$') {
            Write-Host "已中止。处理完上面的问题再部署, 或用 .\deploy.ps1 -Force 强上。" -ForegroundColor Red
            Pause-Exit
            exit 1
        }
        Write-Host "  已确认, 继续。" -ForegroundColor DarkGray
    }
}

# [1/6] 本地 build 前端 —— v1.7.584 事故后改: 前端在【本机】build, 服务器不再跑 npm/vite build。
# 根因: 服务器现场 npm install+vite build 吃满小机器内存(3.5G) → 系统卡死 → SSH/后端全挂(2026-07-05 事故)。
# 本机内存足, build 随便跑; 生成的 frontend/dist 随包上传, 服务器只解压+装Python依赖, 永不再因build卡死。
Write-Host "[1/7] Building frontend locally..." -ForegroundColor Yellow
Push-Location "$PROJECT_DIR\frontend"
Invoke-Native npm run build
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "Local frontend build failed! (修好前端构建再部署, 不上半成品)" -ForegroundColor Red
    Pause-Exit
    exit 1
}
Pop-Location
Write-Host "  Done" -ForegroundColor Green

# [2/6] 本地 build 官网 (site/dist) —— 与前端同理, 一律本机 build。
# site/ 是独立的 Vite 工程(官网), 首次部署前需先在 site/ 下 npm install。
Write-Host "[2/7] Building site (官网) locally..." -ForegroundColor Yellow
if (Test-Path "$PROJECT_DIR\site\package.json") {
    Push-Location "$PROJECT_DIR\site"
    if (-not (Test-Path "$PROJECT_DIR\site\node_modules")) {
        Write-Host "  site/node_modules 缺失, 先装依赖..." -ForegroundColor DarkGray
        Invoke-Native npm install
        if ($LASTEXITCODE -ne 0) {
            Pop-Location
            Write-Host "Site npm install failed!" -ForegroundColor Red
            Pause-Exit
            exit 1
        }
    }
    Invoke-Native npm run build
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-Host "Site build failed! (修好官网构建再部署)" -ForegroundColor Red
        Pause-Exit
        exit 1
    }
    Pop-Location
    Write-Host "  Done" -ForegroundColor Green
} else {
    Write-Host "  Skipped (无 site/)" -ForegroundColor DarkGray
}

Write-Host "[3/7] Packing files (含本地build好的 frontend/dist + site/dist)..." -ForegroundColor Yellow
$excludes = @(
    "node_modules", "__pycache__", ".superpowers", "trading.db",
    ".git", ".claude", "deploy.bat", "deploy.ps1",   # 注: 不再排除 frontend/dist —— 要把本机build好的dist随包上传
    "trading-deploy.tar.gz", "test_api.py",
    "config.json",  # 生产环境配置(含 pushplus_token 等服务器侧手填密钥): 裸名匹配, 绝不上传覆盖, 否则会把生产 token 冲空(2026-06 PushPlus 失推事故根因)
    "bt_cache"   # 回测缓存(4957只日线): 裸名匹配, 防本地旧缓存上传覆盖服务器刚刷新的; 服务器侧另刷
)
$excludeArgs = $excludes | ForEach-Object { "--exclude=$_" }
Push-Location $PROJECT_DIR
Invoke-Native tar @excludeArgs -czf $ARCHIVE .
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "Pack failed!" -ForegroundColor Red
    Pause-Exit
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host "[4/7] Uploading..." -ForegroundColor Yellow
Invoke-Native scp -i $KEY $ARCHIVE "${USER}@${SERVER}:${REMOTE_DIR}/trading-deploy.tar.gz"
Pop-Location
if ($LASTEXITCODE -ne 0) {
    Write-Host "Upload failed!" -ForegroundColor Red
    Pause-Exit
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host "[5/7] Extract + Python deps (服务器不再build前端)..." -ForegroundColor Yellow
# 注: 清理归档用绝对路径 $REMOTE_DIR/...; 不用裸文件名 rm trading-deploy.tar.gz —— 后者会被本地命令安全扫描误判成删除根目录 /trading-deploy.tar.gz 而拦截整条命令
# 依赖装进服务实际运行的 venv(systemd 跑 $REMOTE_DIR/venv/bin/uvicorn), 不要用系统 pip --break-system-packages
# ——后者装到系统 python, venv 看不到, 新依赖会哑火(2026-06-30 pywencai 中招根因)。venv pip 无需 --break-system-packages。
# v1.7.584 事故后: 服务器只解压(含本机build好的dist)+装Python依赖, 【删掉 npm install+vite build】永不再吃内存卡死。
Invoke-Native ssh -i $KEY "${USER}@${SERVER}" "cd $REMOTE_DIR && tar -xzf trading-deploy.tar.gz && rm -f $REMOTE_DIR/trading-deploy.tar.gz && $REMOTE_DIR/venv/bin/pip install -q -r requirements.txt 2>&1 | tail -3"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Extract/deps failed!" -ForegroundColor Red
    Pause-Exit
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host "[6/7] Restarting service..." -ForegroundColor Yellow
Invoke-Native ssh -i $KEY "${USER}@${SERVER}" "systemctl restart trading-monitor && systemctl status trading-monitor --no-pager -l | head -5"
# 这里原本无脑打 Done 不查退出码 —— 重启失败也照样往下走并宣布 Deploy OK
if ($LASTEXITCODE -ne 0) {
    Write-Host "Restart failed! (ssh/systemctl exit $LASTEXITCODE)" -ForegroundColor Red
    Pause-Exit
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Remove-Item "$PROJECT_DIR\$ARCHIVE" -ErrorAction SilentlyContinue

# ── [7/7] 部署后自检 ────────────────────────────────────────────────────
# 此前脚本跑完只打一句 "Deploy OK!", 【不做任何验证】—— 服务起没起来、新前端到底有没有上线,
# 全靠人事后手查。三条硬指标一次查完:
#   1. 服务 active(不是 activating/failed);
#   2. 关键接口不是 502/503(401 是正常的, 说明后端活着只是要登录);
#   3. 线上 index.html 引用的 js 文件名 == 本机刚 build 出来的那个(证明新前端真上线了,
#      而不是解压失败/传了旧包 —— 这是"部署成功但页面还是老的"唯一靠谱的判据)。
# 后端启动要拉行情做初始化, 起来前接口会短暂 502, 所以带重试最多等 40 秒。
# 远程命令刻意【不含任何引号】: PS 5.1 往原生 exe 传带引号的参数会被重新转义, 极易变形。
# 本机 index 名只有字母数字和 -. , 直接当 grep 模式安全。
Write-Host "[7/7] Verifying deployment..." -ForegroundColor Yellow
$localIndex = ''
$idxPath = "$PROJECT_DIR\frontend\dist\index.html"
if (Test-Path $idxPath) {
    $m = [regex]::Match((Get-Content $idxPath -Raw), 'index-[A-Za-z0-9_-]+\.js')
    if ($m.Success) { $localIndex = $m.Value }
}
# 重试循环必须【同时】等 systemd 状态和接口: 只等接口不够 —— 实测 systemctl restart 期间服务会
# 在 deactivating 停留一阵, 这时采样拿到的是 deactivating + 502, 那是重启中间态不是故障。
# 上限 18 次 x 5 秒 = 90 秒(停机可能几十秒, 启动还要拉行情初始化 20-40 秒)。
$probe = 'for i in $(seq 1 18); do ST=$(systemctl is-active trading-monitor); CODE=$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1/api/stocks); if [ $ST = active ]; then case $CODE in 000|502|503) ;; *) break ;; esac; fi; sleep 5; done; echo ACTIVE=$ST; echo HTTP=$CODE; echo INDEX=$(grep -c __INDEX__ __DIR__/frontend/dist/index.html)'
$probe = $probe.Replace('__DIR__', $REMOTE_DIR).Replace('__INDEX__', $localIndex)
$out = Invoke-Native ssh -i $KEY "${USER}@${SERVER}" $probe
$probeExit = $LASTEXITCODE

$active = ($out | Where-Object { $_ -like 'ACTIVE=*' }) -replace '^ACTIVE=', ''
$http   = ($out | Where-Object { $_ -like 'HTTP=*' })   -replace '^HTTP=', ''
$idxHit = ($out | Where-Object { $_ -like 'INDEX=*' })  -replace '^INDEX=', ''

# 自检取不到数据必须【判失败】, 不能当通过 —— 第一版就栽在这: ssh 返回空, 三个值全空,
# 结果每条断言都"没不满足", 于是打印 Done + Deploy OK。没验成 ≠ 验过了。
$fail = @()
if ($probeExit -ne 0)                 { $fail += "自检 ssh 失败(exit $probeExit), 没能验到任何东西" }
if (-not $active -or -not $http)      { $fail += "自检没拿到服务状态/接口返回码, 无法确认部署结果" }
elseif ($active -ne 'active')         { $fail += "服务状态 = $active (应为 active)" }
elseif ($http -in @('000','502','503')) { $fail += "接口 HTTP $http (后端没起来 / nginx 转发不到)" }
if ($localIndex -and $idxHit -ne '1') { $fail += "线上 index.html 没有引用本机刚 build 的 $localIndex (新前端没上线, grep 命中=$idxHit)" }
if (-not $localIndex)                 { $fail += "本机 frontend/dist/index.html 里找不到 index-*.js, 无法核对前端是否真上线" }

Write-Host "    服务: $(if ($active) { $active } else { '(没取到)' })" -ForegroundColor DarkGray
Write-Host "    接口: HTTP $(if ($http) { $http } else { '(没取到)' })  (401=要登录, 属正常)" -ForegroundColor DarkGray
Write-Host "    前端: $localIndex $(if ($idxHit -eq '1') { '已上线' } else { '未匹配' })" -ForegroundColor DarkGray

if ($fail.Count -gt 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "  部署自检未通过!" -ForegroundColor Red
    $fail | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "  查日志: ssh 上去 journalctl -u trading-monitor -n 50 --no-pager" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    Write-Host ""
    Pause-Exit
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Deploy OK!  http://$SERVER" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Pause-Exit
