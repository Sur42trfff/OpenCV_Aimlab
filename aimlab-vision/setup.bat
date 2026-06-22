@echo off
cd /d "%~dp0"
echo Using Python 3.12 (recommended). Python 3.14 venv often fails on Windows.
echo.

if exist ".venv\Scripts\python.exe" (
    echo Removing old .venv ...
    rmdir /s /q .venv 2>nul
    if exist .venv (
        echo Could not delete .venv - close any terminal using it, then run setup again.
        pause
        exit /b 1
    )
)

py -3.12 -m venv .venv
if errorlevel 1 (
    echo venv failed. Try: py -3.12 -m venv .venv
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Setup done. Run run.bat or calibrate.bat
pause
