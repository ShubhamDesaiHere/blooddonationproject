@echo off
echo ============================================================
echo 🩸 BLOOD DONATION SYSTEM - UNIFIED STARTUP
echo ============================================================
echo Starting both Flask and FastAPI services...
echo.
echo 📱 Flask App: http://localhost:5001
echo 🔊 FastAPI Voice API: http://localhost:8000
echo 📚 API Docs: http://localhost:8000/docs
echo.
echo Press Ctrl+C to stop all services
echo ============================================================
echo.

REM Start Flask in background
start "Flask App" cmd /k "cd Final Project\zzzz && python app.py"

REM Wait a moment for Flask to start
timeout /t 3 /nobreak >nul

REM Start FastAPI
start "FastAPI Voice API" cmd /k "uvicorn finalproject4:app --reload --port 8000"

echo.
echo ✅ Both services are starting...
echo 📱 Flask App: http://localhost:5001
echo 🔊 FastAPI Voice API: http://localhost:8000
echo.
echo Press any key to exit this window (services will continue running)
pause >nul
