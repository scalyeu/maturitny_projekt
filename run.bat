@echo off
title AtletCoach
echo ==============================================
echo  STARTUJEM APLIKACIU
echo ==============================================

cd /d "%~dp0"

:: Kontrola Pythonu
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [CHYBA] Python nie je nainstalovany alebo nie je v PATH.
    echo Stiahni Python z https://www.python.org/downloads/
    pause
    exit /b
)

:: Vytvor venv ak neexistuje
IF NOT EXIST venv (
    echo [INFO] Vytvaram virtualne prostredie...
    python -m venv venv
    IF %ERRORLEVEL% NEQ 0 (
        echo [CHYBA] Nepodarilo sa vytvorit virtualne prostredie.
        pause
        exit /b
    )
)

:: Aktivacia
echo [INFO] Aktivujem prostredie...
call venv\Scripts\activate.bat

:: Upgrade pip (ticho)
python -m pip install --upgrade pip --quiet

:: Instalacia balikov
echo [INFO] Instalujem moduly (moze trvat minutu)...
pip install -r requirements.txt --quiet
IF %ERRORLEVEL% NEQ 0 (
    echo [CHYBA] Problem pri instalacii kniznic.
    pause
    exit /b
)

echo [OK] Vsetko nainstalovane.

:: Spustenie Flask servera
echo [INFO] Spustam server...
start "Flask Server" cmd /k "cd /d %~dp0 && venv\Scripts\python.exe app.py"

:: Cakanie kym server nabehne
timeout /t 3 >nul

:: Otvorenie prehliadaca
echo [INFO] Otvaram prehliadac...
start http://127.0.0.1:5001

echo.
echo Ak sa stranka neotvorila, chod manualne na:
echo http://127.0.0.1:5001
echo.
echo Server bezi v osobitnom okne. Zatvor ho ked skoncis.
pause
