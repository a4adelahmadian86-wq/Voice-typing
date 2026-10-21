$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command runflare -ErrorAction SilentlyContinue)) {
    Write-Host "Installing the official Runflare CLI..."
    Invoke-WebRequest -Uri "https://get.runflare.com/install.bat" -OutFile (Join-Path $env:TEMP "runflare-install.bat")
    & (Join-Path $env:TEMP "runflare-install.bat")
    if ($LASTEXITCODE -ne 0) { throw "Runflare CLI installation failed. Install it from https://runflare.com/docs/get-started/ and rerun this script." }
}

Write-Host "Deploying FARAST AI Gateway from: $PSScriptRoot"
Write-Host "If the old three-month service was deleted, select the newly created project/service when the CLI asks."
runflare deploy
if ($LASTEXITCODE -ne 0) { throw "Runflare deploy failed. Check CLI output and Runflare service logs." }
Write-Host "Deployment command completed. Verify /health and authenticated /v1/status before connecting production traffic."
