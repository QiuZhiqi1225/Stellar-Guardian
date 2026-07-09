@echo off
setlocal

cd /d "%~dp0"
if "%PORT%"=="" set PORT=8000
if "%BACKEND_HOST%"=="" set BACKEND_HOST=0.0.0.0

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create virtual environment.
    pause
    exit /b 1
  )
)

echo [2/3] Installing dependencies...
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)

echo [3/3] Starting backend on http://%BACKEND_HOST%:%PORT% ...
call ".venv\Scripts\python.exe" -m uvicorn app.main:app --host %BACKEND_HOST% --port %PORT% --reload
if errorlevel 1 (
  echo Backend exited with an error.
  pause
  exit /b 1
)
