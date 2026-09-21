# API REST del proyecto (Flask)

Sirve por HTTP los dos modelos de `train.py`/`train_shot.py` sobre los
datos ya guardados en `data/processed/basketball.db`: **resultado de
partido** (`models/game_outcome_logreg.joblib` / `game_outcome_xgb.joblib`)
y **probabilidad de que un tiro entre** (`models/shot_make_logreg.joblib`
/ `shot_make_xgb.joblib`, añadido en una segunda fase). Es de solo
lectura, igual que `app/app.py`: ninguna ruta escribe en la base de
datos.

## Arrancar

Requiere haber entrenado los modelos antes (si `models/` esta vacio,
las rutas correspondientes devuelven un error JSON explicito en vez de
fallar en silencio):

```
python train.py         # modelo de resultado de partido
python train_shot.py    # modelo de probabilidad de tiro
```

Luego:

```
pip install -r requirements.txt   # ya incluye flask, scikit-learn, xgboost, joblib
python api/app.py
```

Escucha en <http://127.0.0.1:5060>. Puerto configurable sin tocar el
codigo con la variable de entorno `PORT` (p. ej. `PORT=5061 python
api/app.py`), igual que `app/app.py`.

## Endpoints

- `GET /health` -- `{"status": "ok"}`, para comprobar que el proceso
  responde.
- `GET /model/info` -- metadata real del ultimo entrenamiento
  (`models/game_outcome_metadata.json`): metricas de baseline/regresion
  logistica/XGBoost, `feature_cols`, `min_prior_games`, desglose por
  fuente... mas `available_models` (que `.joblib` existen realmente en
  disco ahora mismo).
- `GET /teams?source=acb` -- lista `{team_id, name}` de los equipos de
  `source` en la base de datos, para saber que `team_id` usar en
  `/predict` sin mirar la base de datos a mano.
- `POST /predict` -- cuerpo JSON:
  ```json
  {
    "source": "acb",
    "home_team_id": "...",
    "away_team_id": "...",
    "model": "xgboost",
    "home_rest_days": 0,
    "away_rest_days": 0
  }
  ```
  `source`/`home_team_id`/`away_team_id` son obligatorios; el resto son
  opcionales (`model` por defecto `"xgboost"`, el otro valor valido es
  `"logreg"`). Devuelve `home_win_probability`/`away_win_probability`
  mas un array `warnings` (p. ej. si algun equipo lleva pocos partidos
  jugados esta temporada todavia, misma exigencia -- `min_prior_games`,
  5 por defecto -- que usa `train.py` para construir el dataset de
  entrenamiento).
- `GET /model/info/shot` -- igual que `/model/info` pero para el modelo
  de tiro: metricas, `sources` (las fuentes con las que se entreno de
  verdad -- ahora mismo NBA, Euroliga, EuroCup y NextGen, ver mas
  abajo), `sanity_check_by_zone`...
- `POST /predict_shot` -- cuerpo JSON:
  ```json
  {
    "source": "nba",
    "x_m": 2.5,
    "y_m": 5.0,
    "shot_type": "2",
    "model": "xgboost"
  }
  ```
  `x_m`/`y_m` son coordenadas en **metros con origen en el aro** (misma
  convencion que `loc_x_m`/`loc_y_m` en `build_shot_features()`, NO las
  unidades nativas que guarda cada scraper en la tabla `shots` --
  decimas de pie en NBA, centimetros en Euroliga/EuroCup, porcentaje de
  cancha completa en FEB). `shot_type` es `"2"` o `"3"` para NBA/
  Euroliga/EuroCup/NextGen -- para las 7 competiciones FEB (ver mas
  abajo) tiene que ser `"desconocido"` en su lugar. Devuelve
  `make_probability` mas `zone`/`dist_m`/`angle_deg` ya calculados con
  las mismas formulas que `src/features.py`.

  **Solo fuentes con coordenadas de tiro CALIBRADAS y con las que el
  modelo se entreno de verdad** (`GET /model/info/shot` -> `sources`
  dice cuales exactamente): a fecha de esta version, 11 -- `nba`,
  `euroleague`, `eurocup`, `nextgen` y las 7 competiciones FEB (`feb`,
  `lfendesa`, `lfchallenge`, `lf2`, `segundafeb`, `tercerafeb`,
  `copaespaa`), calibradas y reentrenadas en una fase posterior a la
  primera version de este endpoint.

  Las 7 FEB tuvieron su propio capitulo: al calibrarlas se descubrieron
  dos bugs reales en `FebSource.get_shots()` (`src/scraper/feb.py`) --
  `made` salia SIEMPRE `True` (`bool("0")` es `True` en Python, "m" es
  un string) y `shot_type` guardaba el reloj del partido, no el tipo de
  tiro (la API de FEB no reporta eso por tiro suelto, a diferencia de
  NBA/Euroliga). Corregido, verificado en vivo contra un boxscore real
  (FGM/FGA exactos), y reingerido de verdad por el usuario
  (`ingest_historical.py --no-cache`, necesario porque la cache de
  `data/raw/` guardaba el dato ya mal parseado) antes de reentrenar --
  ver el aviso "REINGESTA CONFIRMADA 2026-09-21" en `train_shot.py`.
  Como su API no reporta un tipo de tiro real por tiro suelto, se
  entrenaron con `shot_type="desconocido"` fijo, y `/predict_shot`
  rechaza con un 400 cualquier `"2"`/`"3"` para estas 7 fuentes en vez
  de dejar que el modelo generalice sobre una categoria que nunca vio
  en ese contexto (ver `SOURCES_WITHOUT_REPORTED_SHOT_TYPE` en
  `src/predict.py`).

Ejemplo de error (equipo sin partidos jugados, fuente desconocida,
`home_team_id == away_team_id`, modelo inexistente, o modelos sin
entrenar todavia): siempre un JSON `{"error": "..."}` con codigo 400 o
404 -- nunca un 500 sin explicar (ver `PredictionError` en
`src/predict.py`).

## Como calcula la prediccion

Reutiliza las MISMAS formulas de ritmo/cuatro factores que
`app/app.py`/`train.py`/el notebook (`build_team_game_features()`,
`src/features.py`) -- no hay una segunda implementacion de las formulas
aqui. La diferencia con el dataset de entrenamiento
(`src/features_pregame.py`) es que ahi cada fila es un partido YA
JUGADO con la media de partidos ANTERIORES a el; aqui el partido es
HIPOTETICO (los dos equipos ni siquiera tienen por que haberse
enfrentado nunca), asi que se usa la media de TODOS los partidos que
cada equipo ha jugado ya en su temporada mas reciente -- ver el
docstring de `src/predict.py` para el detalle completo.

No se conoce la fecha real de un partido que no se ha jugado, asi que
`rest_days` (la feature con menos peso de las nueve del modelo, ver
coeficientes/importancias que imprime `train.py`) se asume igual para
ambos equipos por defecto (`diff_rest_days = 0`) -- se puede afinar con
`home_rest_days`/`away_rest_days` en el body si interesa.

## Validado

- **Mecanica** (`scripts/test_api.py`, parte 1): base de datos SQLite
  de prueba fabricada a mano (3 equipos, 8 partidos sinteticos, NO datos
  reales de ninguna fuente) + un modelo de mentira solo para probar la
  forma de entrada/salida de `predict_proba` -- comprueba que las medias
  pre-partido salen correctas, que el aviso de "poco historial" aparece
  cuando corresponde, y que los 4 casos de error esperados
  (mismo equipo local/visitante, equipo inexistente, fuente sin datos,
  modelo desconocido) lanzan `PredictionError` en vez de reventar.
- **Integracion real** (`scripts/test_api.py`, parte 2, SOLO si ya
  existen los `.joblib` reales correspondientes): cliente de pruebas de
  Flask (sin levantar el servidor) contra la base de datos real
  (`data/processed/basketball.db`) -- `GET /health`, `/model/info`,
  `/teams?source=nba`, un `POST /predict` real entre dos equipos NBA
  reales, `GET /model/info/shot`, `POST /predict_shot` real (aro y
  triple de NBA, mas uno con `shot_type="desconocido"` de FEB) mas
  peticiones mal formadas para comprobar los 400. Ejecutado de verdad
  en esta sesion: `home_win_probability=0.4654` para Atlanta Hawks
  (local) - Boston Celtics (visitante); para tiro (modelo con 11
  fuentes, accuracy=0.632/AUC=0.654), `make_probability=0.9457` junto
  al aro de NBA vs `0.0018` en un triple muy de frente y justo pasada
  la linea -- el primero es razonable, el segundo probablemente
  refleja mas el AUC modesto del modelo que la realidad del baloncesto
  (deberia rondar 30-35%, no 0.2%) -- limitacion real del modelo
  (features limitadas a distancia/angulo/zona/tipo/fuente, sin contexto
  del defensor ni del tirador), no un bug de como el API lo sirve. No
  se ha comprobado ningun numero contra una casa de apuestas ni fuente
  externa (no es esa la funcion de esta prueba).
- **Calibracion y limpieza de las 7 competiciones FEB** (sesion
  2026-09-20/21): la calibracion de coordenadas se valido con la
  distribucion real de distancias al aro (pico junto al aro, valle
  justo antes de la linea de 3, repunte despues -- patron real de
  cualquier grafico de tiro, ver comentario en `SHOT_COORD_CALIBRATION`
  de `src/features.py`). Tras la reingesta real del usuario, se
  verifico con `zone_fg_pct()` que el FG% por zona cae en rango
  conocido (aro 59-68%, triple_arco 27-35% en las 7 competiciones) antes
  de reentrenar `train_shot.py` con ellas.
