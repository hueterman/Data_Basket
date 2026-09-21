@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM Ingesta semanal de las fuentes con temporada 2026-27 EN CURSO
REM (o a punto de empezar): acb, feb, lfendesa, lfchallenge, lf2,
REM ligau, copaespaa, segundafeb, tercerafeb.
REM
REM Deliberadamente NO incluye nba/wnba/euroleague/eurocup (sus
REM temporadas mas recientes ya estan cerradas -- reingerirlas cada
REM semana con --no-cache no aportaria nada nuevo, solo tiempo/rate
REM limit desperdiciado) ni cbb (excluida por coste, no por
REM temporada, ver docstring de scripts/ingest_data.py).
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

python scripts\ingest_data.py --sources acb,feb,lfendesa,lfchallenge,lf2,ligau,copaespaa,segundafeb,tercerafeb --no-cache > "%LOGFILE%" 2>&1
set EXITCODE=%ERRORLEVEL%

echo Ingesta semanal terminada (exit=%EXITCODE%) -- log completo en %LOGFILE%

endlocal
exit /b %EXITCODE%
