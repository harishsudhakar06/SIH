# BhuRaksha - start the backend (command center + five mobile apps + API).
# Binds 0.0.0.0:5000 so phones on the same Wi-Fi can reach the laptop.
param(
    [int]$Port = 5000,
    [switch]$Build   # rebuild the two frontends before starting
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if ($Build) {
    Push-Location (Join-Path $root 'frontend'); npm run build; Pop-Location
    Push-Location (Join-Path $root 'mobile');   npm run build; Pop-Location
}

$env:BHURAKSHA_PORT = $Port
$lan = (Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -First 1).IPAddress

Write-Host 'BhuRaksha starting...' -ForegroundColor Cyan
if ($lan) {
    Write-Host "  command center : http://$lan`:$Port/"
    Write-Host "  mobile apps    : http://$lan`:$Port/app/"
    Write-Host "  API            : http://$lan`:$Port/api"
    Write-Host "  (phones must use the LAN URL above, never localhost)"
} else {
    Write-Host '  http://127.0.0.1:' $Port '/  (no LAN IP detected)'
}
Write-Host '  demo accounts  : admin/admin123, citizen/citizen123, volunteer/volunteer123,'
Write-Host '                   leader/leader123, response/response123, analyst/analyst123'
Write-Host ''

python (Join-Path $root 'backend\app.py')
