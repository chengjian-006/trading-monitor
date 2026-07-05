$ErrorActionPreference = "Stop"

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
    Read-Host "Press Enter to exit"
    exit 1
}

# [1/5] 本地 build 前端 —— v1.7.584 事故后改: 前端在【本机】build, 服务器不再跑 npm/vite build。
# 根因: 服务器现场 npm install+vite build 吃满小机器内存(3.5G) → 系统卡死 → SSH/后端全挂(2026-07-05 事故)。
# 本机内存足, build 随便跑; 生成的 frontend/dist 随包上传, 服务器只解压+装Python依赖, 永不再因build卡死。
Write-Host "[1/5] Building frontend locally..." -ForegroundColor Yellow
Push-Location "$PROJECT_DIR\frontend"
& npm run build
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "Local frontend build failed! (修好前端构建再部署, 不上半成品)" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Pop-Location
Write-Host "  Done" -ForegroundColor Green

Write-Host "[2/5] Packing files (含本地build好的 frontend/dist)..." -ForegroundColor Yellow
$excludes = @(
    "node_modules", "__pycache__", ".superpowers", "trading.db",
    ".git", ".claude", "deploy.bat", "deploy.ps1",   # 注: 不再排除 frontend/dist —— 要把本机build好的dist随包上传
    "trading-deploy.tar.gz", "test_api.py",
    "config.json",  # 生产环境配置(含 pushplus_token 等服务器侧手填密钥): 裸名匹配, 绝不上传覆盖, 否则会把生产 token 冲空(2026-06 PushPlus 失推事故根因)
    "bt_cache"   # 回测缓存(4957只日线): 裸名匹配, 防本地旧缓存上传覆盖服务器刚刷新的; 服务器侧另刷
)
$excludeArgs = $excludes | ForEach-Object { "--exclude=$_" }
Push-Location $PROJECT_DIR
& tar $excludeArgs -czf $ARCHIVE .
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    Write-Host "Pack failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host "[3/5] Uploading..." -ForegroundColor Yellow
& scp -i $KEY $ARCHIVE "${USER}@${SERVER}:${REMOTE_DIR}/trading-deploy.tar.gz"
Pop-Location
if ($LASTEXITCODE -ne 0) {
    Write-Host "Upload failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host "[4/5] Extract + Python deps (服务器不再build前端)..." -ForegroundColor Yellow
# 注: 清理归档用绝对路径 $REMOTE_DIR/...; 不用裸文件名 rm trading-deploy.tar.gz —— 后者会被本地命令安全扫描误判成删除根目录 /trading-deploy.tar.gz 而拦截整条命令
# 依赖装进服务实际运行的 venv(systemd 跑 $REMOTE_DIR/venv/bin/uvicorn), 不要用系统 pip --break-system-packages
# ——后者装到系统 python, venv 看不到, 新依赖会哑火(2026-06-30 pywencai 中招根因)。venv pip 无需 --break-system-packages。
# v1.7.584 事故后: 服务器只解压(含本机build好的dist)+装Python依赖, 【删掉 npm install+vite build】永不再吃内存卡死。
& ssh -i $KEY "${USER}@${SERVER}" "cd $REMOTE_DIR && tar -xzf trading-deploy.tar.gz && rm -f $REMOTE_DIR/trading-deploy.tar.gz && $REMOTE_DIR/venv/bin/pip install -q -r requirements.txt 2>&1 | tail -3"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Extract/deps failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host "[5/5] Restarting service..." -ForegroundColor Yellow
& ssh -i $KEY "${USER}@${SERVER}" "systemctl restart trading-monitor && systemctl status trading-monitor --no-pager -l | head -5"
Write-Host "  Done" -ForegroundColor Green

Remove-Item "$PROJECT_DIR\$ARCHIVE" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Deploy OK!  http://$SERVER" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to exit"
