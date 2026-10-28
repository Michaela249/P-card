@echo off
setlocal
cd /d "%~dp0"

if not exist "pcards.db" (
  echo The required pcards.db file is missing from this folder.
  echo Put pcards.db beside this starter file, then try again.
  pause
  exit /b 1
)

set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>nul && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo Python 3 is required to run this local app.
  echo Install Python from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Preparing the local app for its first run...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo The local Python environment could not be created.
    pause
    exit /b 1
  )
)

echo Checking the app requirements...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo The app requirements could not be installed.
  pause
  exit /b 1
)

start "P-card Audit Explorer server" /min "%CD%\.venv\Scripts\python.exe" app.py
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:5000
echo.
echo P-card Audit Explorer is running at http://127.0.0.1:5000
echo Keep the separate P-card Audit Explorer server window open while using the app.
timeout /t 5 /nobreak >nul
