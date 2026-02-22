@echo off
setlocal
cd /d "%~dp0"

if not exist "src\zdash\stronghold\StrongholdCrackerMain.java" (
  echo [ERROR] Missing source file: src\zdash\stronghold\StrongholdCrackerMain.java
  exit /b 1
)

if not exist "build\classes" mkdir "build\classes"

echo [INFO] Compiling stronghold cracker...
javac -encoding UTF-8 -cp "lib/*" -d "build/classes" "src/zdash/stronghold/StrongholdCrackerMain.java"
if errorlevel 1 (
  echo [ERROR] Compilation failed.
  exit /b 1
)

echo [INFO] Built classes in tools\stronghold_cracker\build\classes
exit /b 0
