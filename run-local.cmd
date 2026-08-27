@echo off
setlocal

powershell -NoProfile -Command "try { $response = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3000/api/health -TimeoutSec 3; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if %errorlevel% equ 0 (
	echo Call Center API is already running at http://127.0.0.1:3000
	exit /b 0
)

if not exist .env.local (
	echo Missing .env.local. Set GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID before starting the app.
	exit /b 1
)
findstr /b /c:"GOOGLE_SERVICE_ACCOUNT_JSON=" .env.local >nul
if errorlevel 1 (
	echo .env.local is missing GOOGLE_SERVICE_ACCOUNT_JSON.
	exit /b 1
)
findstr /b /c:"GOOGLE_SHEET_ID=" .env.local >nul
if errorlevel 1 (
	echo .env.local is missing GOOGLE_SHEET_ID.
	exit /b 1
)

echo Starting Call Center API at http://127.0.0.1:3000
python -m uvicorn api.index:app --host 127.0.0.1 --port 3000 --env-file .env.local
