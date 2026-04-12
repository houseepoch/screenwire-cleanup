@echo off
setlocal

set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..\..") do set REPO_ROOT=%%~fI

cd /d "%REPO_ROOT%"
py -3 build_architecture_reports.py %*
