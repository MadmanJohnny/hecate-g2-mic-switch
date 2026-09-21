<#
.SYNOPSIS
    按窗口标题截图，正确处理 Windows 高 DPI 缩放。

.DESCRIPTION
    为什么需要这个脚本：

    当显示器缩放不是 100%（例如 125%）时，Windows 会对「非 DPI 感知」的进程
    做坐标虚拟化。此时同一个进程里会出现两套坐标系：

        GetWindowRect()            -> 虚拟（被缩放过的）坐标
        Graphics.CopyFromScreen()  -> 真实物理像素坐标

    两者混用就会导致截图偏移，偏移量为：
        窗口坐标 × (1 - 1/缩放比例)
    例如窗口在虚拟坐标 (338,338)、缩放 125%，真实位置是物理 (422,422)，
    若仍按 (338,338) 去抓像素，画面就会整体错位约 84 像素。

    本脚本改用 DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)，
    它返回的是**真实物理像素**的窗口边框；顺带还排除了 Windows 10/11 窗口
    四周那圈看不见的调整边框（约 7px），所以既不会偏移也不会多出桌面背景。

.EXAMPLE
    pwsh -File tools/capture_window.ps1 -Title "*HECATE*" -Out docs/images/screenshot-main.png

.EXAMPLE
    pwsh -File tools/capture_window.ps1 -Title "记事本*" -Out note.png -Activate
#>
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [string]$Out = "window.png",
    [switch]$Activate,
    [int]$WaitMs = 700
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

if (-not ('CapWin' -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class CapWin {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT r, int size);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int L; public int T; public int R; public int B; }
    public const int DWMWA_EXTENDED_FRAME_BOUNDS = 9;
}
"@
}

# 1) 找到目标窗口
$proc = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like $Title } |
        Select-Object -First 1
if (-not $proc) {
    throw "找不到标题匹配 '$Title' 的窗口。当前可见窗口标题：`n" +
          ((Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowTitle } |
            Select-Object -ExpandProperty MainWindowTitle | Select-Object -First 20) -join "`n")
}

$h = $proc.MainWindowHandle
Write-Host ("目标窗口: [{0}] {1}  (PID {2})" -f $proc.ProcessName, $proc.MainWindowTitle, $proc.Id)

if ($Activate) {
    if ([CapWin]::IsIconic($h)) { [void][CapWin]::ShowWindow($h, 9) }   # SW_RESTORE
    [void][CapWin]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds $WaitMs
}

# 2) 取真实物理像素的可见边框（排除不可见的调整边框）
$r = New-Object CapWin+RECT
$hr = [CapWin]::DwmGetWindowAttribute($h, [CapWin]::DWMWA_EXTENDED_FRAME_BOUNDS, [ref]$r, 16)
$src = 'DWM 物理边框'
if ($hr -ne 0 -or ($r.R - $r.L) -le 0) {
    # 老系统没有该属性时退回到 GetWindowRect（注意：此时会多出约 7px 的不可见边框）
    [void][CapWin]::GetWindowRect($h, [ref]$r)
    $src = 'GetWindowRect（回退，可能含约 7px 不可见边框）'
}

$w = $r.R - $r.L
$hh = $r.B - $r.T
Write-Host ("坐标来源: {0}" -f $src)
Write-Host ("截图区域: ({0},{1}) - ({2},{3})   尺寸 {4} x {5}" -f $r.L, $r.T, $r.R, $r.B, $w, $hh)

# 3) 截取（CopyFromScreen 用的就是物理像素，与上面的坐标同一空间）
$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size $w, $hh), [System.Drawing.CopyPixelOperation]::SourceCopy)
$g.Dispose()

$full = [IO.Path]::GetFullPath($Out)
$dir = Split-Path -Parent $full
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$bmp.Save($full, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()

Write-Host ("已保存: {0}  ({1:N0} 字节)" -f $full, (Get-Item $full).Length)
