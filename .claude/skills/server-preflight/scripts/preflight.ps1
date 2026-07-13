# run.py'nin debug=True + Werkzeug reloader kullanması yüzünden eski
# python süreçleri arka planda canlı kalabilir; yeni bir tane başlatmadan
# önce hepsini kapatmak gerekir (feedback-flask-server-testing.md).
#
# Kullanım:
#   pwsh .claude/skills/server-preflight/scripts/preflight.ps1            # sadece eskileri kapat
#   pwsh .claude/skills/server-preflight/scripts/preflight.ps1 -Start     # kapat + run.py'yi arka planda başlat + hazır olana kadar bekle

param(
    [switch]$Start,
    [int]$Port = 5000,
    [int]$TimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"

$old = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'run\.py' }

if ($old) {
    Write-Host "Kapatılıyor: $($old.Count) eski run.py süreci"
    $old | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
    Start-Sleep -Seconds 1
} else {
    Write-Host "Eski run.py süreci yok."
}

if (-not $Start) {
    exit 0
}

Write-Host "run.py başlatılıyor (arka planda)..."
$proc = Start-Process -FilePath "python" -ArgumentList "run.py" -PassThru -WindowStyle Hidden
Write-Host "PID: $($proc.Id)"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$up = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode) { $up = $true; break }
    } catch {
        if ($_.Exception.Response) { $up = $true; break }  # 302/401 gibi kodlar da "ayakta" demektir
    }
    Start-Sleep -Milliseconds 500
}

if ($up) {
    Write-Host "Sunucu ayakta: http://127.0.0.1:$Port/"
    exit 0
} else {
    Write-Host "UYARI: $TimeoutSeconds sn içinde sunucu yanıt vermedi (PID $($proc.Id) hâlâ çalışıyor olabilir, logu kontrol et)."
    exit 1
}
