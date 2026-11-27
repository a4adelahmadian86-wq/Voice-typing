@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo FARAST - Runflare Gateway Deployment
echo ================================================
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-runflare-stack.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo DEPLOYMENT FAILED. Review the message above.
) else (
  echo.
  echo Deployment command completed. Check the service health before use.
)
echo.
pause
exit /b %RC%
