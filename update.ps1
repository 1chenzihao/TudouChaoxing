# 土豆刷学习通 · 一键热更新（Windows PowerShell）
#
# 用法：双击同目录的 update.bat；或在配置工具里点「一键更新代码」
#   powershell -ExecutionPolicy Bypass -File update.ps1
#
# 关键点：更新源永远是上游仓库，不是你自己 fork 出来的那个。
#   - 你是原作者（origin 就是上游）  -> git 直连，最快
#   - 你是 fork 的                    -> 自动挂一个 upstream 远程，从上游 reset
#   - 你下的是 ZIP、没有 .git 或没装 git -> 从上游下 ZIP 覆盖
#   （以前只看 origin/main，fork 的人 reset 完还是自己的旧代码，永远更新不到）
#
# 它不会碰：config.ini、cookies.txt、cache.json、retry_pending.json、各种日志

$ErrorActionPreference = "Stop"
$Upstream = "yyzbuhaohe/TudouChaoxing"
$Branch   = "main"
$ZipUrl   = "https://github.com/$Upstream/archive/refs/heads/$Branch.zip"
$Root     = Split-Path -Parent $MyInvocation.MyCommand.Path

$Keep = @("config.ini", "cookies.txt", "cache.json", "retry_pending.json", "chaoxing.log", "run.log", "scan.log")

function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c }
function Ok($m)   { Say "  [OK]   $m" "Green" }
function Warn($m) { Say "  [注意] $m" "Yellow" }
function Die($m)  { Say "  [失败] $m" "Red"; exit 1 }

# 有些机器根本没装 git（下 ZIP 来用的那批人），所有 git 调用都会抛
# "The term 'git' is not recognized"，必须提前判断，否则脚本当场死掉。
$HasGit = [bool](Get-Command git.exe -ErrorAction SilentlyContinue)

# 统一包一层：git 的报错不该让整个更新崩掉，拿退出码自己判断
function Run-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & git @GitArgs 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return [pscustomobject]@{ Code = $code; Out = ($out -join [Environment]::NewLine) }
}

function Get-Remote([string]$name) {
    if (-not $HasGit) { return "" }
    $r = Run-Git remote get-url $name
    if ($r.Code -eq 0) { return $r.Out.Trim() }
    return ""
}

Say "==============================================" "Cyan"
Say "  土豆刷学习通 · 一键更新" "Cyan"
Say "  目录: $Root" "Cyan"
Say "  上游: $Upstream" "Cyan"
Say "==============================================" "Cyan"

Set-Location $Root

if (-not (Test-Path "main.py")) { Die "当前目录没有 main.py，请把本脚本放到项目根目录再运行" }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Die "没找到 python，先双击「一键配置.bat」自动装一个" }
Ok ("Python: " + ((python --version) 2>&1))

# ---------- 备份 ----------
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

# ---------- 更新 ----------
$updated = $false
$oldRev  = "?"

if ((Test-Path ".git") -and (-not $HasGit)) {
    Say ""
    Warn "这台机器没装 git，改用下载 ZIP 覆盖（效果一样）"
}

if ((Test-Path ".git") -and $HasGit) {
    $r = Run-Git rev-parse --short HEAD
    if ($r.Code -eq 0) { $oldRev = $r.Out.Trim() }

    $originUrl  = Get-Remote "origin"
    $isUpstream = ($originUrl -and ($originUrl -match [regex]::Escape($Upstream)))
    $remoteName = "origin"

    if (-not $isUpstream) {
        Say ""
        Say "检测到这是 fork 出来的仓库，更新源改成上游（不碰你自己的 origin）" "Cyan"
        if (-not (Get-Remote "upstream")) {
            Run-Git remote add upstream "https://github.com/$Upstream.git" | Out-Null
        }
        $remoteName = "upstream"
    } else {
        Say ""
        Say "方式：git 直连上游（你就是上游本体）" "Cyan"
    }

    $f = Run-Git fetch $remoteName $Branch --depth=1 --quiet
    if ($f.Code -eq 0) {
        $rs = Run-Git reset --hard "$remoteName/$Branch" --quiet
        if ($rs.Code -eq 0) {
            $r2 = Run-Git rev-parse --short HEAD
            $newRev = "?"
            if ($r2.Code -eq 0) { $newRev = $r2.Out.Trim() }
            if ($oldRev -eq $newRev) { Ok "已经是最新版本 ($newRev)" } else { Ok "已更新 $oldRev -> $newRev" }
            if (-not $isUpstream) { Ok "本地代码已对齐上游；你 fork 的远端没动（云端要更新看最后一行提示）" }
            $updated = $true
        } else {
            Warn "git reset 失败，改用下载 ZIP"
        }
    } else {
        Warn "git 拉取失败（网络或证书），改用下载 ZIP"
    }
}

# ---------- 兜底：下载上游 ZIP 覆盖 ----------
if (-not $updated) {
    Say ""
    Say "方式：下载上游最新 ZIP 覆盖" "Cyan"
    $tmp = Join-Path $env:TEMP ("tudou-update-" + $stamp)
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp "main.zip"
    Say "  $ZipUrl"
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
            if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                curl.exe -L --ssl-no-revoke -o $zip $ZipUrl
                if ((Test-Path $zip) -and ((Get-Item $zip).Length -gt 10000)) { $okDl = $true }
            }
        }
    }
    if (-not $okDl) { Die "下载失败，可以去 GitHub 页面手动下 ZIP 解压覆盖" }

    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    # ZIP 里只有一个顶层目录（仓库名-main）。fork 改名过、或 zip 结构变了都别死在名字上，
    # 直接找「含 main.py 的那个目录」
    $srcDir = Get-ChildItem $tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "main.py") } | Select-Object -First 1
    if (-not $srcDir) { Die "解压后没找到含 main.py 的目录，ZIP 结构可能变了" }

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

# ---------- 还原配置 ----------
foreach ($f in $Keep) {
    $b = Join-Path $backup $f
    if (Test-Path $b) { Copy-Item $b (Join-Path $Root $f) -Force }
}
if ($hasCfg) { Ok "config.ini 已还原，账号和 key 没被动过" }

# ---------- 依赖 ----------
Say ""
Say "更新依赖（自动挑最快的国内镜像）" "Cyan"
$envPy = Join-Path $Root "env_setup.py"
if (Test-Path $envPy) {
    python $envPy --install
    if ($LASTEXITCODE -eq 0) { Ok "依赖已就绪（上面那份清单就是自检结果）" } else { Warn "依赖安装有报错，看上面最后几行" }
} else {
    python -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
    if ($LASTEXITCODE -eq 0) { Ok "依赖已就绪" } else { Warn "依赖安装有报错" }
}

# ---------- 收尾 ----------
$ver = "未知"
if ((Test-Path ".git") -and $HasGit) {
    $rv = Run-Git log -1 --format="%h %s"
    if ($rv.Code -eq 0 -and $rv.Out.Trim()) { $ver = $rv.Out.Trim() }
}
Say ""
Say "==============================================" "Green"
Say "  更新完成" "Green"
if ($ver -ne "未知") { Say "  当前版本: $ver" "Green" }
Say "  备份: _backup_$stamp（确认没问题后可以删）" "Green"
Say "  下一步: 双击桌面图标，或 python main.py --solve-only" "Green"
Say "==============================================" "Green"
Say ""
Say "  云端（GitHub Actions）跑的是你 fork 里那份代码，" "Yellow"
Say "  想让云端也用新版：去自己仓库点 Sync fork，或在仓库里执行 git push" "Yellow"
