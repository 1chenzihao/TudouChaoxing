# 土豆刷学习通 · 桌面快捷方式创建脚本（由「初始化快捷方式.bat」调用）
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "配置工具.pyw"))) {
    Write-Host "  [失败] 脚本位置不对：没在上层目录找到 配置工具.pyw" -ForegroundColor Red
    exit 1
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  土豆刷学习通 · 创建桌面快捷方式" -ForegroundColor Cyan
Write-Host "  项目目录: $Root" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# 1) 找 pythonw.exe（用 pythonw 才不会每次弹黑框）
$pythonw = $null
$cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if ($cmd) { $pythonw = $cmd.Source }
if (-not $pythonw) {
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py) {
        $cand = Join-Path (Split-Path -Parent $py.Source) "pythonw.exe"
        if (Test-Path $cand) { $pythonw = $cand }
    }
}
if (-not $pythonw) {
    Write-Host "  [失败] 没找到 pythonw.exe" -ForegroundColor Red
    Write-Host "         先把 Python 装上，安装时勾上 Add python.exe to PATH" -ForegroundColor Yellow
    Write-Host "         下载地址: https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK]   Python: $pythonw" -ForegroundColor Green

# 2) 桌面路径（兼容 OneDrive 重定向）
$desktop = [Environment]::GetFolderPath("Desktop")
if (-not $desktop -or -not (Test-Path $desktop)) {
    $desktop = Join-Path $env:USERPROFILE "Desktop"
}
if (-not (Test-Path $desktop)) {
    Write-Host "  [失败] 找不到桌面目录: $desktop" -ForegroundColor Red
    exit 1
}

# 3) 生成 .lnk
$target = Join-Path $Root "配置工具.pyw"
$icon = Join-Path $Root "assets\mascot.ico"
$lnkPath = Join-Path $desktop "土豆刷学习通 配置工具.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$target`""
$shortcut.WorkingDirectory = $Root
$shortcut.Description = "土豆刷学习通 · 图形配置工具"
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.WindowStyle = 1
$shortcut.Save()

if (Test-Path $lnkPath) {
    Write-Host "  [OK]   快捷方式已创建" -ForegroundColor Green
    Write-Host "         $lnkPath" -ForegroundColor Gray
} else {
    Write-Host "  [失败] 快捷方式没写成功，可能被安全软件拦了" -ForegroundColor Red
    exit 1
}

# 4) 顺手检查依赖装没装（没装只提示，不装东西）
$py = $pythonw -replace "pythonw.exe$", "python.exe"
$missing = @()
foreach ($m in @("requests", "bs4", "lxml", "loguru", "openai")) {
    & $py -c "import $m" 2>$null
    if ($LASTEXITCODE -ne 0) { $missing += $m }
}
if ($missing.Count -gt 0) {
    Write-Host "" 
    Write-Host "  [注意] 这些依赖还没装: $($missing -join ', ')" -ForegroundColor Yellow
    Write-Host "         双击快捷方式前先跑一次: python -m pip install -r requirements.txt" -ForegroundColor Yellow
} else {
    Write-Host "  [OK]   依赖齐全，双击桌面图标就能用" -ForegroundColor Green
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  完成：去桌面双击「土豆刷学习通 配置工具」" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green

if ($Host.Name -eq "ConsoleHost") { Write-Host ""; Read-Host "按回车关闭" | Out-Null }