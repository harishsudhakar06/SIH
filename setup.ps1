# BhuRaksha - one-shot setup for Windows / PowerShell
# Installs Python + Node dependencies, builds the command center and the five
# mobile apps, then trains the local ML model and seeds/assesses the database.
#
#   .\setup.ps1            # full setup
#   .\setup.ps1 -SkipTests # skip the pytest run at the end
param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Step($n, $total, $msg) {
    Write-Host "[$n/$total] $msg" -ForegroundColor Cyan
}

Step 1 5 'Python dependencies'
python -m pip install -r (Join-Path $root 'requirements.txt')

Step 2 5 'Command center (frontend: npm install + build)'
Push-Location (Join-Path $root 'frontend')
npm install --no-fund --no-audit
npm run build
Pop-Location

Step 3 5 'Five mobile apps (npm install + build)'
Push-Location (Join-Path $root 'mobile')
npm install --no-fund --no-audit
npm run build
Pop-Location

Step 4 5 'Database: schema, fixture data, ML model, first assessment pass'
python -c "import sys; sys.path.insert(0, r'backend'); from app import bootstrap; bootstrap()"

if (-not $SkipTests) {
    Step 5 5 'Test suite (pytest)'
    python -m pytest (Join-Path $root 'backend\tests') -q -p no:warnings
} else {
    Step 5 5 'Test suite skipped'
}

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host 'Start everything with:  .\run_backend.ps1'
