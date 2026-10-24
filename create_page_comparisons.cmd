@echo off
setlocal
"%~dp0venv\Scripts\python.exe" "%~dp0create_page_comparisons.py" %*
if errorlevel 1 (
    echo Comparison generation failed.
    pause
    exit /b 1
)
echo Comparison generation completed.
pause
