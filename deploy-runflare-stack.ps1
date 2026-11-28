param([switch]$SkipLocalFallback)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not (Get-Command runflare -ErrorAction SilentlyContinue)) {
    Write-Host "Installing the official Runflare CLI..."
    Invoke-WebRequest -Uri "https://get.runflare.com/install.bat" -OutFile (Join-Path $env:TEMP "runflare-install.bat")
    & (Join-Path $env:TEMP "runflare-install.bat")
    if ($LASTEXITCODE -ne 0) { throw "Runflare CLI installation failed. See https://runflare.com/docs/get-started/" }
}

Write-Host ""
Write-Host "STEP 1/2: Deploy the authenticated gateway first (Cloudflare Workers AI is the primary provider)."
Write-Host "Before connecting traffic, set CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, and FARAST_GATEWAY_TOKEN in the gateway service settings."
Write-Host "The gateway can operate without the local fallback when Cloudflare is configured."
Push-Location (Join-Path $root "runflare-gateway")
try {
    runflare reset
    if ($LASTEXITCODE -ne 0) { throw "Could not reset the cached Runflare deploy root." }
    Write-Host "Select the Runflare project, then the gateway service. Select runflare-gateway as this service's deploy root."
    runflare deploy
    if ($LASTEXITCODE -ne 0) { throw "Gateway deployment failed." }
} finally { Pop-Location }

if ($SkipLocalFallback) {
    Write-Host ""
    Write-Host "Skipping the optional local Qwen fallback as requested."
} else {
    Write-Host ""
    Write-Host "STEP 2/2: Attempt to deploy the optional local Qwen fallback."
    Write-Host "This service downloads about 1.28 GB from Hugging Face on first start; it may fail if outbound access or free resources are unavailable."
    try {
        Push-Location (Join-Path $root "runflare-llm")
        try {
            runflare reset
            if ($LASTEXITCODE -ne 0) { throw "Could not reset the cached Runflare deploy root." }
            Write-Host "Select the same Runflare project, then the local-model service. Select runflare-llm as this service's deploy root."
            runflare deploy
            if ($LASTEXITCODE -ne 0) { throw "Local LLM deployment failed." }
        } finally { Pop-Location }
    } catch {
        Write-Warning "The gateway has been deployed, but the optional local fallback did not deploy: $($_.Exception.Message)"
        Write-Warning "Cloudflare remains primary. Configure FARAST_LOCAL_LLM_URL and FARAST_LOCAL_LLM_API_KEY only after the local model's /health returns HTTP 200."
    }
}

Write-Host ""
Write-Host "Deployment commands completed. Verify gateway /health, /ready, and authenticated /v1/status."
Write-Host "Then send a real authenticated /v1/chat/completions request and transcribe a real Persian recording before routing production traffic."
