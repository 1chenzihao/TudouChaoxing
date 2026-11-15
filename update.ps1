# 土豆刷学习通 · 一键热更新（Windows PowerShell）
#
# 用法：双击同目录的 update.bat 就行；或在命令行执行
#   powershell -ExecutionPolicy Bypass -File update.ps1
#
# 它做什么：备份 config.ini 等你的数据 -> 拉最新代码（有 .git 用 git pull，没有就下 ZIP）
#           -> 把代码覆盖进当前目录 -> 装依赖 -> 自检 -> 汇报
# 它不会碰：config.ini、cookies.txt、cache.json、retry_pending.json、各种日志

$ErrorActionPreference = "Stop"
$Repo   = "yyzbuhaohe/TudouChaoxing"
$Branch = "main"
$ZipUrl = "https://github.com/$Repo/archive/refs/heads/$Branch.zip"
$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path

$Keep = @("config.ini", "cookies.txt", "cache.json", "retry_pending.json", "chaoxing.log", "run.log", "scan.log")

function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c }
function Ok($m)   { Say "  [OK]   $m" "Green" }
function Warn($m) { Say "  [注意] $m" "Yellow" }
function Die($m)  { Say "  [失败] $m" "Red"; exit 1 }

Say "==============================================" "Cyan"
Say "  土豆刷学习通 · 一键更新" "Cyan"
Say "  目录: $Root" "Cyan"
Say "==============================================" "Cyan"

Set-Location $Root

if (-not (Test-Path "main.py")) { Die "当前目录没有 main.py，请把本脚本放到项目根目录再运行" }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Die "没找到 python，先装 Python 并勾上 Add python.exe to PATH" }
Ok ("Python: " + ((python --version) 2>&1))

$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $Root "_backup_$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
$hasCfg = $false
foreach ($f in $Keep) {
    if (Test-Path $f) {
        Copy-Item $f (Join-Path $backup $f) -Force
        if ($f -eq "config.ini") { $hasCfg = $true }
    }
}
if ($hasCfg) { Ok "已备份 config.ini 等数据 -> _backup_$stamp" } else { Warn "没找到 config.ini，更新后需要自己复制一份模板" }

$updated = $false
if (Test-Path ".git") {
    Say ""
    Say "方式：git pull" "Cyan"
    $old = ""
    try { $old = (git rev-parse --short HEAD) } catch { }
    try {
        git fetch --all --quiet 2>&1 | Out-Null
        git reset --hard "origin/$Branch" --quiet 2>&1 | Out-Null
        $new = (git rev-parse --short HEAD)
        if ($old -eq $new) { Ok "已经是最新版本 ($new)" } else { Ok "已更新 $old -> $new" }
        $updated = $true
    } catch {
        Warn "git pull 失败（网络或证书问题），改用下载 ZIP 的方式"
    }
}

if (-not $updated) {
    Say ""
    Say "方式：下载最新 ZIP 覆盖" "Cyan"
    $tmp = Join-Path $env:TEMP ("tudou-update-" + $stamp)
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp "main.zip"
    Say "  下载 $ZipUrl"
    $okDl = $false
    try {
        Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing -TimeoutSec 300
        $okDl = $true
    } catch {
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing -TimeoutSec 300
            $okDl = $true
        } catch {
            # 第三种：用系统自带的 curl（能绕开部分证书吊销检查失败）
            if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                curl.exe -L --ssl-no-revoke -o $zip $ZipUrl
                if ((Test-Path $zip) -and ((Get-Item $zip).Length -gt 10000)) { $okDl = $true }
            }
            if (-not $okDl) { Die ("下载失败: " + $_.Exception.Message + "  可以去 GitHub 手动下 ZIP") }
        }
    }
    if (-not $okDl) { Die "下载失败" }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $srcDir = Get-ChildItem $tmp -Directory | Where-Object { $_.Name -like "*TudouChaoxing*" } | Select-Object -First 1
    if (-not $srcDir) { Die "解压后没找到代码目录，ZIP 结构可能变了" }
    $skipNames = @("config.ini", "cookies.txt", "cache.json", "retry_pending.json", "chaoxing.log", "run.log", "scan.log")
    $script:copied = 0
    Get-ChildItem $srcDir.FullName -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($srcDir.FullName.Length + 1)
        if ($rel -like "_backup_*") { return }
        if ($rel -match "^\.git") { return }
        if ($skipNames -contains $_.Name) { return }
        $target = Join-Path $Root $rel
        $dir = Split-Path -Parent $target
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        Copy-Item $_.FullName $target -Force
        $script:copied = $script:copied + 1
    }
    Ok "已覆盖 $script:copied 个文件"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

foreach ($f in $Keep) {
    $b = Join-Path $backup $f
    if (Test-Path $b) { Copy-Item $b (Join-Path $Root $f) -Force }
}
if ($hasCfg) { Ok "config.ini 已还原，账号和 key 没被动过" }

Say ""
Say "更新依赖（清华源）" "Cyan"
python -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
if ($LASTEXITCODE -eq 0) { Ok "依赖已就绪" } else { Warn "依赖安装有报错，看最后一行红字；一般不影响已经装好的环境" }

Say ""
Say "自检" "Cyan"
$missing = 0
foreach ($m in @("requests", "bs4", "lxml", "loguru", "openai")) {
    python -c "import $m" 2>$null
    if ($LASTEXITCODE -ne 0) { Warn "缺少模块: $m"; $missing++ }
}
if ($missing -eq 0) { Ok "依赖齐全，可以开跑" }

Say ""
Say "==============================================" "Green"
Say "  更新完成" "Green"
Say "  备份: _backup_$stamp（确认没问题后可以删）" "Green"
Say "  下一步: python main.py --solve-only    或    python main.py" "Green"
Say "  要回滚: 把 _backup_$stamp 里的文件复制回来" "Green"
Say "==============================================" "Green"