@echo off
rem StudyRAG launcher: double-click to start the app in your browser. Close this window to stop it.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\streamlit.exe" (
    echo The Python environment is missing. Set it up first, see "Quick start" in README.md:
    echo     python -m venv .venv  ^&^&  .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example
)

rem With the local LLM (the default), make sure Ollama is running.
findstr /r /c:"^LLM_PROVIDER=groq" /c:"^LLM_PROVIDER=openai" /c:"^LLM_PROVIDER=claude" .env >nul 2>&1
if errorlevel 1 call :start_ollama

if defined STUDYRAG_HEADLESS (set HEADLESS=true) else (set HEADLESS=false)
echo.
echo   StudyRAG is starting at http://localhost:8501
echo   Close this window to stop it.
echo.
".venv\Scripts\streamlit.exe" run app.py --server.headless=%HEADLESS%
exit /b %errorlevel%

:start_ollama
powershell -NoProfile -Command "try { Invoke-RestMethod http://localhost:11434/api/version -TimeoutSec 2 | Out-Null } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 exit /b 0
set "OLLAMA_APP="
for /f "delims=" %%i in ('where ollama 2^>nul') do if not defined OLLAMA_APP set "OLLAMA_APP=%%~dpiollama app.exe"
if not defined OLLAMA_APP set "OLLAMA_APP=%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
if exist "%OLLAMA_APP%" (
    echo Starting Ollama...
    start "" "%OLLAMA_APP%"
    powershell -NoProfile -Command "Start-Sleep 6"
) else (
    echo Ollama was not found. Install it from https://ollama.com, or set LLM_PROVIDER=groq in .env.
)
exit /b 0
