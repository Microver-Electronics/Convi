@echo off
REM Convi - KiCad 10 to Altium Designer converter (GUI wizard)
cd /d "%~dp0"
py -m convi gui
if errorlevel 1 (
  echo.
  echo Convi stopped with an error - see the message above.
  pause
)
