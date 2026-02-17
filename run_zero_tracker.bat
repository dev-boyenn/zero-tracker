@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "REPO_URL=https://github.com/dev-boyenn/zero-tracker.git"
set "REPO_DIR=%SCRIPT_DIR%zero-tracker"
if exist "%SCRIPT_DIR%\.git" if exist "%SCRIPT_DIR%run_dashboard.py" set "REPO_DIR=%SCRIPT_DIR%"

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Git is not installed or not on PATH.
  pause
  exit /b 1
)

if exist "%REPO_DIR%\.git" (
  echo [INFO] Repository found at:
  echo        %REPO_DIR%
  pushd "%REPO_DIR%" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Failed to enter repo directory.
    pause
    exit /b 1
  )
  echo [INFO] Pulling latest changes...
  git pull --ff-only
  if errorlevel 1 (
    set "PULL_EXIT=!ERRORLEVEL!"
    popd >nul
    echo [ERROR] git pull failed.
    pause
    exit /b !PULL_EXIT!
  )
  popd >nul
) else (
  if exist "%REPO_DIR%" (
    echo [ERROR] "%REPO_DIR%" exists but is not a git repository.
    pause
    exit /b 1
  )
  echo [INFO] Cloning repository...
  git clone "%REPO_URL%" "%REPO_DIR%"
  if errorlevel 1 (
    echo [ERROR] git clone failed.
    pause
    exit /b 1
  )
)

if not exist "%REPO_DIR%\\run_dashboard.py" (
  echo [ERROR] Could not find run_dashboard.py in:
  echo        %REPO_DIR%
  pause
  exit /b 1
)

echo [INFO] Starting dashboard...
pushd "%REPO_DIR%" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Failed to enter repo directory.
  pause
  exit /b 1
)

set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"
if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  popd >nul
  echo [ERROR] Python is not installed or not on PATH.
  pause
  exit /b 1
)

if not exist "requirements.txt" (
  popd >nul
  echo [ERROR] Could not find requirements.txt in:
  echo        %REPO_DIR%
  pause
  exit /b 1
)

echo [INFO] Installing/updating Python requirements...
call %PY_CMD% -m pip install -r requirements.txt
if errorlevel 1 (
  set "PIP_EXIT=!ERRORLEVEL!"
  popd >nul
  echo [ERROR] Failed to install requirements.
  pause
  exit /b !PIP_EXIT!
)

call %PY_CMD% run_dashboard.py
set "RUN_EXIT=!ERRORLEVEL!"
popd >nul
if not "!RUN_EXIT!"=="0" (
  echo [ERROR] Dashboard exited with code !RUN_EXIT!.
  pause
  exit /b !RUN_EXIT!
)
exit /b 0
