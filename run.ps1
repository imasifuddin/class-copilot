# class-copilot launcher
#   .\run.ps1                 start it
#   .\run.ps1 --mode mic      listen through the laptop microphone instead
#   .\run.ps1 --local-only    do not expose it to the wifi
#   .\run.ps1 --forget-devices  unpair every phone
Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "First run: creating the virtual environment (a few minutes)..." -ForegroundColor Cyan
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env for your API key." -ForegroundColor Yellow
    Write-Host "You can paste a key in there, or just set it later from the app's"
    Write-Host "settings screen. Starting now either way..."
    Write-Host ""
}

.\.venv\Scripts\python.exe -m app.main @args
