@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual env missing. Run setup.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python -m src.main
pause
