@echo off
setlocal
set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not exist "%BASH%" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not exist "%BASH%" set "BASH=%LocalAppData%\Programs\Git\bin\bash.exe"
if not exist "%BASH%" (
  echo Git Bash not found; install Git for Windows
  exit /b 1
)
cd /d "%~dp0"
"%BASH%" "%~dp0scripts\program.sh" %*
exit /b %ERRORLEVEL%
