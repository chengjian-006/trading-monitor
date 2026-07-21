# 多会话并行开工用: 每个 AI 窗口一个隔离工作区(git worktree + 独立分支), 互不踩磁盘文件。
# 约定工作流: worktree 里开工 → done 合回 main → push → 再从 main 跑 deploy.ps1。
# 详见记忆 concurrent-session-deploy / 判断口径见 parallel-window-workflow。
#
# 用法:
#   .\worktree.ps1 new  <名字>     新建隔离工作区 ..\tm-<名字> + 分支 feat/<名字>, 并打开
#   .\worktree.ps1 list            列出当前所有 worktree
#   .\worktree.ps1 done <名字>     把 feat/<名字> 合回 main(--no-ff) 并清掉工作区; 之后自己 push+deploy
#   .\worktree.ps1 drop <名字>     不合并直接丢弃工作区+分支(改废了才用)

param(
    [Parameter(Position = 0)][string]$Cmd,
    [Parameter(Position = 1)][string]$Name
)
$ErrorActionPreference = "Stop"

$REPO   = Split-Path -Parent $MyInvocation.MyCommand.Path      # 主仓库(main 常驻, 别在这开工)
$PARENT = Split-Path -Parent $REPO                             # 同级目录, worktree 放这

# 跑 git 只按退出码判成败, 不让 stderr 参与(PS5.1 会把 git 的进度/提示 stderr 包成 ErrorRecord
# 触发 Stop, 参考 deploy.ps1 的同款坑)。
function RunGit {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & git @args } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') 失败 (exit $LASTEXITCODE)" }
}

function Require-Name {
    if ([string]::IsNullOrWhiteSpace($Name)) { Write-Host "缺少 <名字>" -ForegroundColor Red; exit 1 }
}

Set-Location $REPO

switch ($Cmd) {
    'new' {
        Require-Name
        $wt     = Join-Path $PARENT "tm-$Name"
        $branch = "feat/$Name"
        if (Test-Path $wt) { Write-Host "$wt 已存在" -ForegroundColor Red; exit 1 }

        Write-Host "同步 origin/main ..." -ForegroundColor Cyan
        RunGit fetch origin
        # 新分支从最新的 origin/main 起, 不带主仓库工作区里可能有的别人未提交改动。
        RunGit worktree add -b $branch $wt origin/main

        Write-Host ""
        Write-Host "已建隔离工作区:" -ForegroundColor Green
        Write-Host "  目录 : $wt"
        Write-Host "  分支 : $branch (基于 origin/main)"
        Write-Host ""
        Write-Host "在这个窗口的 Claude 里把工作目录切到上面这个目录再开工。" -ForegroundColor Yellow
        Write-Host "干完回来跑:  .\worktree.ps1 done $Name" -ForegroundColor Yellow
    }

    'list' {
        RunGit worktree list
    }

    'done' {
        Require-Name
        $wt     = Join-Path $PARENT "tm-$Name"
        $branch = "feat/$Name"
        if (-not (Test-Path $wt)) { Write-Host "$wt 不存在" -ForegroundColor Red; exit 1 }

        # 工作区必须干净, 不然合回去会漏东西。
        Push-Location $wt
        $dirty = (& git status --porcelain)
        Pop-Location
        if ($dirty) {
            Write-Host "$branch 工作区还有未提交改动, 先在那个窗口 commit 完再来:" -ForegroundColor Red
            $dirty | ForEach-Object { Write-Host "  $_" }
            exit 1
        }

        Write-Host "合 $branch 回 main ..." -ForegroundColor Cyan
        RunGit fetch origin
        RunGit checkout main
        RunGit merge --ff-only origin/main          # 先让本地 main 追平远端, 落后就先追上
        RunGit merge --no-ff $branch -m "merge $branch"

        Write-Host "清理工作区 + 分支 ..." -ForegroundColor Cyan
        RunGit worktree remove $wt
        RunGit branch -d $branch

        Write-Host ""
        Write-Host "已合回 main。接下来自己确认:" -ForegroundColor Green
        Write-Host "  1. git log --oneline origin/main..HEAD   # 看要推什么" -ForegroundColor Yellow
        Write-Host "  2. git push" -ForegroundColor Yellow
        Write-Host "  3. .\deploy.ps1                          # 从 main 部署(脚本自带预检)" -ForegroundColor Yellow
    }

    'drop' {
        Require-Name
        $wt     = Join-Path $PARENT "tm-$Name"
        $branch = "feat/$Name"
        Write-Host "丢弃 $wt 和分支 $branch (不合并) ..." -ForegroundColor Yellow
        RunGit worktree remove --force $wt
        RunGit branch -D $branch
        Write-Host "已丢弃。" -ForegroundColor Green
    }

    default {
        Write-Host "用法:" -ForegroundColor Cyan
        Write-Host "  .\worktree.ps1 new  <名字>   新建隔离工作区 + 分支, 在新窗口里把工作目录切过去开工"
        Write-Host "  .\worktree.ps1 list          列出所有 worktree"
        Write-Host "  .\worktree.ps1 done <名字>   合回 main + 清工作区(之后自己 push + deploy)"
        Write-Host "  .\worktree.ps1 drop <名字>   不合并直接丢弃(改废了才用)"
    }
}
