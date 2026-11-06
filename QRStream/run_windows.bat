@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 goto error
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
".venv\Scripts\python.exe" sender.py %*
goto end
:error
echo Setup or launch failed. Check that Python 3.10+ is installed and Tkinter is available.
pause
:end
endlocal
