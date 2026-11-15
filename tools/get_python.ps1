# -*- coding: utf-8 -*-
# 土豆刷学习通 · 从国内镜像下载并静默安装 Python（不碰官网）
#
# 为什么单独写一个 ps1：这一步要在"机器上还没有 Python"的时候跑，
# 所以不能用 python 来下载 python。
#
# 用法：  powershell -NoProfile -ExecutionPolicy Bypass -File tools\get_python.ps1

$ErrorActionPreference = 'Stop'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$Version = '3.12.7'
$File = "python-$Version-amd64.exe"
$Mirrors = @(
    @{ Name = '华为云'; Url = "https://mirrors.huaweicloud.com/python/$Version/$File" },
    @{ Name = 'npmmirror'; Url = "https://registry.npmmirror.com/-/binary/python/$Version/$File" },
    @{ Name = '阿里云'; Url = "https://mirrors.aliyun.com/python-release/windows/$File" }
)
$Dest = Join-Path $env:TEMP $File

Write-Host ""
Write-Host "  土豆刷学习通 · 安装 Python $Version"
Write-Host "  ------------------------------------------------------------"
Write-Host "  只会从国内镜像下载（华为云 / npmmirror / 阿里云），连不上官网也行。"
Write-Host ""

$ok = $false
foreach ($m in $Mirrors) {
    Write-Host "  尝试 $($m.Name)：$($m.Url)"
    try {
        if (Test-Path $Dest) { Remove-Item $Dest -Force }
        Invoke-WebRequest -Uri $m.Url -OutFile $Dest -TimeoutSec 60 -UseBasicParsing
        $size = (Get-Item $Dest).Length
        if ($size -lt 5MB) {
            Write-Host "  [跳过] 下到的文件只有 $([math]::Round($size/1MB,1)) MB，不像安装包"
            continue
        }
        Write-Host "  下载完成：$([math]::Round($size/1MB,1)) MB"
        $ok = $true
        break
    } catch {
        Write-Host "  [失败] $($m.Name)：$($_.Exception.Message)"
    }
}

if (-not $ok) {
    Write-Host ""
    Write-Host "  国内镜像都没下下来。两个办法："
    Write-Host "    1. 换个网络再双击一次本文件（公司网/校园网偶尔会拦大文件）"
    Write-Host "    2. 让能上网的机器下好 $File，拷到本机双击安装"
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "  开始静默安装（不需要管理员权限，装到当前用户目录）..."
$args = @(
    '/quiet',
    'InstallAllUsers=0',
    'PrependPath=1',
    'Include_pip=1',
    'Include_launcher=1',
    'Include_test=0'
)
$p = Start-Process -FilePath $Dest -ArgumentList $args -Wait -PassThru
if ($p.ExitCode -ne 0) {
    Write-Host "  [失败] 安装包返回 $($p.ExitCode)。可以手动双击 $Dest 安装。"
    exit $p.ExitCode
}

Write-Host "  [完成] Python $Version 装好了。"
Write-Host ""
Write-Host "  下一步：重新双击「一键配置.bat」，在「环境」页点「一键安装依赖」。"
Write-Host ""
exit 0
