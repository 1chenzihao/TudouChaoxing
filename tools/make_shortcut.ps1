# 土豆刷学习通 · 初始化（桌面快捷方式 + 环境体检）
#
# 由「初始化快捷方式.bat」调用。做三件事：
#   1. 找到能用的 Python；一个都没有就从国内镜像装一个（不碰官网）
#   2. 在桌面建「土豆刷学习通 配置工具」图标（用 pythonw，双击不弹黑框）
#   3. 报一遍依赖装没装
#
# 注意：本文件必须存成 UTF-8 with BOM，否则 PowerShell 5.1 会按 GBK 读，中文全乱。

param(
    # 配置工具里调用时传 -NoPause，否则结尾那个「按回车关闭」会把调用方挂住
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "配置工具.pyw"))) {
    Write-Host "  [失败] 脚本位置不对：没在上层目录找到 配置工具.pyw" -ForegroundColor Red
    exit 1
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  土豆刷学习通 · 初始化" -ForegroundColor Cyan
Write-Host "  项目目录: $Root" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---------- 1) 找 Python（跳过应用商店的占位版） ----------
function Find-Python {
    # 优先用 py 启动器：它知道所有已安装版本，且不会指向 WindowsApps 的假货
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $exe = & py.exe -3 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) {
            $w = Join-Path (Split-Path -Parent $exe) "pythonw.exe"
            if (Test-Path $w) { return $w }
            return $exe
        }
    }
    foreach ($name in @("pythonw.exe", "python.exe")) {
        foreach ($c in @(Get-Command $name -All -ErrorAction SilentlyContinue)) {
            if ($c.Source -and $c.Source -notmatch "WindowsApps") {
                $w = Join-Path (Split-Path -Parent $c.Source) "pythonw.exe"
                if (Test-Path $w) { return $w }
                return $c.Source
            }
        }
    }
    # PATH 没刷新的情况（刚装完 Python）：扫常见安装位置
    $cands = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe"),
        "C:\Python312\pythonw.exe",
        "C:\Python313\pythonw.exe"
    )
    foreach ($p in $cands) { if (Test-Path $p) { return $p } }
    return $null
}

$pythonw = Find-Python
if (-not $pythonw) {
    Write-Host "  [..]   这台机器上没有可用的 Python，先从国内镜像装一个（不用去官网）" -ForegroundColor Yellow
    $installer = Join-Path $Root "tools\get_python.ps1"
    if (Test-Path $installer) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [失败] Python 没装上。手动双击「安装Python.bat」再试。" -ForegroundColor Red
            exit 1
        }
        $pythonw = Find-Python
    }
}
if (-not $pythonw) {
    Write-Host "  [失败] 还是找不到 pythonw.exe" -ForegroundColor Red
    Write-Host "         双击「安装Python.bat」装一个，再回来跑本脚本。" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK]   Python: $pythonw" -ForegroundColor Green

# ---------- 2) 桌面路径（兼容 OneDrive 重定向） ----------
$desktop = [Environment]::GetFolderPath("Desktop")
if (-not $desktop -or -not (Test-Path $desktop)) {
    $desktop = Join-Path $env:USERPROFILE "Desktop"
}
if (-not (Test-Path $desktop)) {
    Write-Host "  [失败] 找不到桌面目录: $desktop" -ForegroundColor Red
    exit 1
}

# ---------- 3) 生成 .lnk ----------
$target = Join-Path $Root "配置工具.pyw"
$icon = Join-Path $Root "assets\mascot.ico"
$lnkPath = Join-Path $desktop "土豆刷学习通 配置工具.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $target + '"'
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

# ---------- 4) 依赖体检（交给 env_setup.py，别在这里另写一份包名列表） ----------
$py = $pythonw -replace "pythonw\.exe$", "python.exe"
$envPy = Join-Path $Root "env_setup.py"
if ((Test-Path $envPy) -and (Test-Path $py)) {
    Write-Host ""
    & $py $envPy --check
    $rc = $LASTEXITCODE
    Write-Host ""
    if ($rc -ne 0) {
        Write-Host "  下一步：双击桌面图标，在「⑤ 环境」页点「一键安装依赖」" -ForegroundColor Yellow
        Write-Host "         （会自动挑最快的国内镜像，不用管官网连不连得上）" -ForegroundColor Yellow
    } else {
        Write-Host "  下一步：双击桌面图标，填 ① 账号 和 ② 大模型 就行" -ForegroundColor Green
    }
} else {
    Write-Host "  [注意] 没找到 env_setup.py，依赖状态没法检查" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  完成：去桌面双击「土豆刷学习通 配置工具」" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green

if ($Host.Name -eq "ConsoleHost" -and -not $NoPause) { Write-Host ""; Read-Host "按回车关闭" | Out-Null }
