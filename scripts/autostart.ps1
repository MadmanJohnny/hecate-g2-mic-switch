param([switch]$Remove)
$scriptsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptsDir
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'HECATE G2 语音输入桥接.lnk'

if ($Remove) {
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "已移除开机自启：$lnk" }
    else { Write-Host "本来就没有安装开机自启。" }
    exit 0
}

$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = 'C:\Python314\pythonw.exe' }
if (-not (Test-Path $pythonw)) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { $pythonw = $cmd.Source } else { Write-Host '找不到 pythonw.exe'; exit 1 }
}

$script = Join-Path $root 'src\g2_voice_bridge.py'
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $pythonw
$s.Arguments = '"' + $script + '"'
$s.WorkingDirectory = $root
$s.Description = 'HECATE G2 麦克风开关 -> 输入法语音输入'
$s.Save()
Write-Host "已安装开机自启：$lnk"
Write-Host "下次登录 Windows 会自动静默运行。"
