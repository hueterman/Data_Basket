@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM Ingesta semanal de las fuentes con temporada EN CURSO (o a punto
REM de empezar): acb, feb, lfendesa, lfchallenge, lf2, ligau,
REM copaespaa, segundafeb, tercerafeb, nba, wnba.
REM
REM AÑADIDOS 2026-10-02: nba (2026-27, arranca 20 oct 2026 -- hasta
REM entonces esta linea no hace nada util, mismo caso ya aceptado con
REM varias fuentes FEB antes de que arrancara su propia temporada) y
REM wnba (2026, playoffs en curso AHORA MISMO hasta como mucho el 31
REM de octubre -- pasada esa fecha esta fuente deja de aportar nada
REM nuevo semana a semana, igual que nba/euroleague/eurocup lo eran
REM antes de este cambio, pero se deja la linea puesta sin mas: no
REM hace daño, solo tarda un poco mas en comprobar que no hay nada
REM nuevo). Ver SEASONS en scripts/ingest_data.py.
REM
REM Deliberadamente NO incluye euroleague/eurocup (sus temporadas mas
REM recientes ya estan cerradas) ni cbb (excluida por coste Y por
REM temporada -- la 2026-27 de NCAA arranca a primeros de noviembre,
REM y el problema real de --no-cache rescrapeando TODA la temporada
REM cada semana para miles de partidos sigue sin resolver, ver
REM docstring de scripts/ingest_data.py).
REM
REM --no-cache fuerza rescrapear de verdad en vez de reutilizar
REM data/raw/ -- necesario porque la temporada esta EN CURSO: cada
REM semana hay partidos nuevos que la cache todavia no tiene.
REM
REM Pensado para Windows Task Scheduler (ver instrucciones de
REM registro en el mensaje donde se genero este archivo). NO se
REM puede ejecutar a traves del bridge de Claude -- necesita la
REM salida de red real de este equipo (ver docstring de
REM scripts/ingest_data.py).
REM ============================================================

cd /d D:\Proyectos\Data_Basket

set LOGDIR=logs\ingest_current_seasons
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM Timestamp fiable independiente del idioma/formato regional de
REM Windows (%date%/%time% varian por configuracion regional).
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set TS=%%i
set LOGFILE=%LOGDIR%\%TS%.log

python scripts\ingest_data.py --sources acb,feb,lfendesa,lfchallenge,lf2,ligau,copaespaa,segundafeb,tercerafeb,nba,wnba --no-cache > "%LOGFILE%" 2>&1
set EXITCODE=%ERRORLEVEL%

echo Ingesta semanal terminada (exit=%EXITCODE%) -- log completo en %LOGFILE%

endlocal
exit /b %EXITCODE%
