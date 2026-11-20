@echo off
REM Install Convi and its Python dependencies
cd /d "%~dp0"
py -m pip install -e .
echo.
echo Done. Start the wizard with Convi.bat or run: convi --help
pause
