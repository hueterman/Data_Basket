# App del proyecto (Flask)

Explorador de datos de solo lectura sobre `data/processed/basketball.db`,
más un panel para lanzar y vigilar las ingestas (`scripts/ingest_data.py`
y `scripts/ingest_historical.py`) sin salir del navegador. El panel de
ingestas SÍ escribe en la base de datos — pero indirectamente, lanzando
los mismos scripts de siempre como subproceso; la app en sí sigue sin
tener ninguna ruta que escriba directamente.

## Arrancar

```
pip install -r requirements.txt   # ya incluye flask
python app/app.py
```

Abre <http://127.0.0.1:5050> en el navegador. `Ctrl+C` en la terminal para
pararla.

Si el 5050 también estuviera ocupado, cambia el puerto sin tocar el
código con la variable de entorno `PORT` (p. ej. `PORT=5051 python
app/app.py`).

## Qué tiene

- **Resumen**: cuántas filas hay por tabla y por fuente.
- **Equipos**: lista filtrable + ficha con plantilla y calendario/balance.
- **Jugadores**: lista filtrable y paginada + ficha con boxscore partido a
  partido y medias.
- **Partidos**: lista filtrable por fuente/temporada + ficha con el
  boxscore completo (equipo e individual) y, si la fuente tiene
  coordenadas de tiro calibradas (NBA, Euroliga, EuroCup, WNBA, NextGen),
  el shot chart del partido dibujado al vuelo.
- **Ingestas**: lanza `ingest_data.py` (temporada actual) o
  `ingest_historical.py` (temporadas pasadas) eligiendo fuentes, límite
  de partidos y si traer plantillas, con el log en vivo y un botón de
  detener. Solo una ingesta a la vez (dos escribiendo en la misma
  `basketball.db` a la vez podrían chocar). Debajo, el historial real de
  `ingest_log` (equipos/jugadores/partidos/boxscores por etapa). NextGen
  (ANGT) solo aparece en el formulario histórico, nunca en el simple —
  ver nota abajo.

## Notas

- El shot chart usa las mismas funciones de dibujo de pista que el
  notebook de EDA (extraídas a `src/viz/courts.py` para no duplicarlas) y
  la misma calibración de coordenadas que `src/features.py`
  (`SHOT_COORD_CALIBRATION`) — es una pista simplificada, no el trazado
  oficial completo.
- Se añadió un índice (`idx_player_stats_player`) en
  `src/pipeline/schema.py` para que la ficha de jugador no tenga que
  escanear toda la tabla `player_stats`. Se crea solo (`CREATE INDEX IF
  NOT EXISTS`) la próxima vez que se abra la base de datos — no hace
  falta ningún paso manual.
- Validado offline contra una copia real de `basketball.db` (14.663
  partidos, 199.168 líneas de boxscore, 1,2M tiros) antes de entregarla.
- El panel de ingestas se validó con un script de mentira (sin red real,
  ver aviso de entorno del proyecto) para la mecánica de lanzar/vigilar/
  detener, y los argumentos que construye cada formulario se
  contrastaron contra el `parse_args()` real de `ingest_data.py`/
  `ingest_historical.py` — no se ha probado lanzando una ingesta real
  contra internet desde aquí (eso solo se puede hacer desde tu equipo).
- Euroliga/EuroCup/CBB/WNBA llevan un aviso (⚠️) en el formulario porque
  ya se ha chocado con límites de peticiones reales en esas fuentes (ver
  entradas de rate limit en `decisiones_proyecto.md`) — el panel no
  bloquea lanzarlas, solo avisa. NextGen usa el mismo cliente HTTP y el
  mismo pacing que Euroliga/EuroCup (misma clase `EuroleagueSource`), así
  que probablemente comparte el mismo riesgo, pero no lleva el aviso
  porque no se ha confirmado en vivo específicamente para NextGen (no
  inventamos una confirmación que no existe).
- **NextGen (ANGT), añadido 2026-09-19**: a diferencia del resto de
  fuentes, no tiene una "temporada actual" — cada edición anual son ~5
  códigos de temporada reales (uno por sede clasificatoria + uno para la
  Final Eight, p. ej. `JTB23` = Belgrado 2023-24), no un año simple. Por
  eso solo se puede pedir desde el formulario histórico, con temporadas
  personalizadas (código de sede real, no un año) y una única fuente
  marcada. Para saber qué códigos de sede existen en un año dado no hay
  atajo en la app todavía: hay que consultar
  `https://api-live.euroleague.net/v2/competitions/J/seasons` a mano y
  filtrar por el campo `year` (ver docstring de
  `src/scraper/euroleague.py`, sección NextGen, para el detalle completo
  de lo verificado en vivo).
- **FEB histórico, añadido 2026-09-20**: hasta ahora solo se podía
  ingerir la temporada en curso de FEB (`resultados.aspx`, que ignora
  el parámetro de temporada). Investigación en vivo encontró
  `calendario.aspx`, que SÍ respeta la temporada y además trae toda la
  Liga Regular en una sola petición — y de paso reveló que
  `get_boxscore()` estaba roto para cualquier partido (selectores que
  no existen en el HTML real). Ambas cosas están arregladas (ver
  docstring de `src/scraper/feb.py`, sección "HISTORICO Y BOXSCORE
  RESUELTOS EN VIVO"). FEB ya aparece en el formulario histórico con
  temporadas por defecto (2025 a 2021, año de inicio de temporada,
  mismo criterio que ACB al no tener ninguna temporada actual con
  partidos jugados todavía).
