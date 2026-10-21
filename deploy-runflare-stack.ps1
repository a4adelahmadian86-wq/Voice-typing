$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not (Get-Command runflare -ErrorAction SilentlyContinue)) {
    Write-Host "Installing the official Runflare CLI..."
    Invoke-WebRequest -Uri "https://get.runflare.com/install.bat" -OutFile (Join-Path $env:TEMP "runflare-install.bat")
    & (Join-Path $env:TEMP "runflare-install.bat")
    if ($LASTEXITCODE -ne 0) { throw "Runflare CLI installation failed. See https://runflare.com/docs/get-started/" }
}

Write-Host ""
Write-Host "STEP 1/2: Deploy the local Qwen3 model service."
Write-Host "Runflare remembers the first deploy root. Reset that cached root before EACH service so the two folders do not deploy to the same service."
Push-Location (Join-Path $root "runflare-llm")
try {
    runflare reset
    if ($LASTEXITCODE -ne 0) { throw "Could not reset the cached Runflare deploy root." }
    Write-Host "Select the Runflare project, then the model service. Select runflare-llm as this service's deploy root."
    runflare deploy
    if ($LASTEXITCODE -ne 0) { throw "Local LLM deployment failed." }
} finally { Pop-Location }

Write-Host ""
Write-Host "STEP 2/2: Deploy the FARAST API gateway."
Write-Host "Before connecting production traffic, set FARAST_LOCAL_LLM_URL and FARAST_LOCAL_LLM_API_KEY in the gateway service settings."
Push-Location (Join-Path $root "runflare-gateway")
try {
    runflare reset
    if ($LASTEXITCODE -ne 0) { throw "Could not reset the cached Runflare deploy root." }
    Write-Host "Select the same Runflare project, then the gateway service. Select runflare-gateway as this service's deploy root."
    runflare deploy
    if ($LASTEXITCODE -ne 0) { throw "Gateway deployment failed." }
} finally { Pop-Location }

Write-Host ""
Write-Host "Deploy commands completed. Wait for the model service /health to return 200, then verify gateway /health and authenticated /v1/status."