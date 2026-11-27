$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$gatewayScript = Join-Path $root "runflare-gateway\deploy-runflare.ps1"

if (-not (Test-Path -LiteralPath $gatewayScript)) {
    throw "Canonical Runflare gateway deploy script was not found: $gatewayScript"
}

Write-Host "FARAST Runflare deployment: one stateless gateway service."
Write-Host "The legacy Qwen model service is intentionally not deployed."
Write-Host "All persistent data must remain outside Runflare."
& $gatewayScript
if ($LASTEXITCODE -ne 0) {
    throw "Runflare gateway deployment failed. Review the CLI output and service logs."
}
Write-Host "Deployment command completed. Verify /health, /ready and authenticated /v1/status."
