@echo off
REM ============================================================
REM  baslat.bat  -  iki terminali dogru dizinde ayni anda acar
REM
REM   Terminal 1: run_api.py             (hand_control dizini, sistem Python)
REM   Terminal 2: camera_hand_bridge.py  (.venv icindeki Python 3.12)
REM
REM  Bu dosyayi hand_control klasorune koyun (run_api.py ve .venv ile ayni yer).
REM  Durdurma: kamera penceresinde 'q', API penceresinde Ctrl+C
REM ============================================================
chcp 65001 >nul

REM Bu .bat hangi klasordeyse oraya gec (hand_control).
REM Asagida acilan iki pencere de bu dizini devralir.
cd /d "%~dp0"

REM --- Terminal 1: API sunucusu ---
start "Robot API Server" cmd /k py run_api.py

REM Sunucunun acilip robota baglanmasi icin kisa bekleme
REM (kopru sunucudan once baslarsa ilk komutlar TX:FAIL olur)
timeout /t 6 /nobreak >nul

REM --- Terminal 2: kamera koprusu (venv Python, aktivasyona gerek yok) ---
start "Kamera Koprusu" cmd /k .venv\Scripts\python.exe camera_hand_bridge.py

exit /b 0
