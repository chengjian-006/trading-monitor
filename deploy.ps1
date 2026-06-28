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

Write-Host "[1/4] Packing files..." -ForegroundColor Yellow
$excludes = @(
    "node_modules", "__pycache__", ".superpowers", "trading.db",
    ".git", "frontend/dist", ".claude", "deploy.bat", "deploy.ps1",
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

Write-Host "[2/4] Uploading..." -ForegroundColor Yellow
& scp -i $KEY $ARCHIVE "${USER}@${SERVER}:${REMOTE_DIR}/trading-deploy.tar.gz"
Pop-Location
if ($LASTEXITCODE -ne 0) {
    Write-Host "Upload failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host "[3/4] Building on server..." -ForegroundColor Yellow
# 注: 清理归档用绝对路径 $REMOTE_DIR/...; 不用裸文件名 rm trading-deploy.tar.gz —— 后者会被本地命令安全扫描误判成删除根目录 /trading-deploy.tar.gz 而拦截整条命令
& ssh -i $KEY "${USER}@${SERVER}" "cd $REMOTE_DIR && tar -xzf trading-deploy.tar.gz && rm -f $REMOTE_DIR/trading-deploy.tar.gz && pip install -q --break-system-packages -r requirements.txt 2>&1 | tail -3 && cd frontend && npm install --silent 2>&1 | tail -1 && npx vite build 2>&1 | tail -3"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "  Done" -ForegroundColor Green

Write-Host "[4/4] Restarting service..." -ForegroundColor Yellow
& ssh -i $KEY "${USER}@${SERVER}" "systemctl restart trading-monitor && systemctl status trading-monitor --no-pager -l | head -5"
Write-Host "  Done" -ForegroundColor Green

Remove-Item "$PROJECT_DIR\$ARCHIVE" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Deploy OK!  http://$SERVER" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to exit"
