# 读取指定录音端点（默认列全部）的硬件静音状态，并在状态变化时打印
# 用法: pwsh -File mute_probe.ps1 [-Match HECATE] [-Seconds 300]
param(
  [string]$Match = "HECATE",
  [int]$Seconds = 120,
  [int]$IntervalMs = 150
)

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class CoreAudio {
    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    private class MMDeviceEnumeratorComObject { }

    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceEnumerator {
        int EnumAudioEndpoints(int dataFlow, int dwStateMask, out IMMDeviceCollection ppDevices);
        int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice ppEndpoint);
    }

    [Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceCollection {
        int GetCount(out int pcDevices);
        int Item(int nDevice, out IMMDevice ppDevice);
    }

    [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDevice {
        int Activate(ref Guid iid, int dwClsCtx, IntPtr pActivationParams,
                     [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
        int OpenPropertyStore(int stgmAccess, out IntPtr ppProperties);
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string ppstrId);
        int GetState(out int pdwState);
    }

    [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IAudioEndpointVolume {
        int RegisterControlChangeNotify(IntPtr p);
        int UnregisterControlChangeNotify(IntPtr p);
        int GetChannelCount(out int c);
        int SetMasterVolumeLevel(float f, ref Guid g);
        int SetMasterVolumeLevelScalar(float f, ref Guid g);
        int GetMasterVolumeLevel(out float f);
        int GetMasterVolumeLevelScalar(out float f);
        int SetChannelVolumeLevel(uint ch, float f, ref Guid g);
        int SetChannelVolumeLevelScalar(uint ch, float f, ref Guid g);
        int GetChannelVolumeLevel(uint ch, out float f);
        int GetChannelVolumeLevelScalar(uint ch, out float f);
        int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid g);
        int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
    }

    public class Endpoint {
        public string Id;
        public string Guid;
        public bool Mute;
        public float Level;
    }

    public static Endpoint[] List() {
        var list = new System.Collections.Generic.List<Endpoint>();
        var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDeviceCollection coll;
        int hr = enumerator.EnumAudioEndpoints(1 /*eCapture*/, 1 /*ACTIVE*/, out coll);
        if (hr != 0) throw new COMException("EnumAudioEndpoints", hr);
        int count;
        coll.GetCount(out count);
        for (int i = 0; i < count; i++) {
            IMMDevice dev;
            coll.Item(i, out dev);
            string id;
            dev.GetId(out id);
            var ep = new Endpoint();
            ep.Id = id;
            int a = id.LastIndexOf('{');
            int b = id.LastIndexOf('}');
            ep.Guid = (a >= 0 && b > a) ? id.Substring(a, b - a + 1).ToUpper() : id;
            Guid iid = typeof(IAudioEndpointVolume).GUID;
            object o;
            hr = dev.Activate(ref iid, 1 /*CLSCTX_INPROC_SERVER*/, IntPtr.Zero, out o);
            if (hr == 0 && o != null) {
                var vol = (IAudioEndpointVolume)o;
                bool m; float v;
                if (vol.GetMute(out m) == 0) ep.Mute = m;
                if (vol.GetMasterVolumeLevelScalar(out v) == 0) ep.Level = v;
                Marshal.ReleaseComObject(o);
            }
            list.Add(ep);
            Marshal.ReleaseComObject(dev);
        }
        Marshal.ReleaseComObject(coll);
        Marshal.ReleaseComObject(enumerator);
        return list.ToArray();
    }
}
'@

$eps = [CoreAudio]::List()
Write-Output "=== 全部活动录音端点 ==="
foreach ($e in $eps) { Write-Output ("{0}  mute={1}  level={2:N2}" -f $e.Guid, $e.Mute, $e.Level) }

$target = $null
# 用 MMDevices 注册表把 GUID 映射到友好名
$names = @{}
$base = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture'
Get-ChildItem $base -ErrorAction SilentlyContinue | ForEach-Object {
  $g = $_.PSChildName.ToUpper()
  if (-not $g.StartsWith('{')) { $g = '{' + $g + '}' }
  $p = Join-Path $_.PSPath 'Properties'
  if (Test-Path $p) {
    $pr = Get-ItemProperty $p -ErrorAction SilentlyContinue
    $n = $pr.'{b3f8fa53-0004-438e-9003-51a46e139bfc},6'
    if ($n) { $names[$g] = $n }
  }
}
Write-Output ""
Write-Output "=== 端点友好名 ==="
foreach ($e in $eps) {
  $n = $names[$e.Guid]
  Write-Output ("{0}  ->  {1}" -f $e.Guid, $n)
  if ($n -and $n -match $Match) { $target = $e }
}
if (-not $target -and $eps.Count -eq 1) {
  $target = $eps[0]
  Write-Output ("（未匹配到 '$Match'，但只有一个活动端点，直接使用它）")
}
if (-not $target) { Write-Output "`n未找到匹配 '$Match' 的端点"; exit 1 }

Write-Output ""
Write-Output ("=== 监听端点 {0}（{1}）静音状态变化 {2} 秒 ===" -f $target.Guid, $names[$target.Guid], $Seconds)
Write-Output "现在请拨动耳机线控上的麦克风开关（关→开→关→开，每次停 2~3 秒）"
Write-Output "格式: 时间  静音=值  音量=值"
$last = "$($target.Mute)|$($target.Level)"
$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
  $eps2 = [CoreAudio]::List()
  $cur = $eps2 | Where-Object { $_.Guid -eq $target.Guid }
  if ($cur) {
    $key = "$($cur.Mute)|$($cur.Level)"
    if ($key -ne $last) {
      Write-Output ("{0}  静音={1}  音量={2:N2}" -f (Get-Date -Format 'HH:mm:ss.fff'), $cur.Mute, $cur.Level)
      $last = $key
    }
  }
  Start-Sleep -Milliseconds $IntervalMs
}
Write-Output "监听结束"
