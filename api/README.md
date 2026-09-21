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

## Documentacion interactiva (Swagger UI)

Con el servidor arrancado, <http://127.0.0.1:5060/docs/> abre una
interfaz donde se puede ver cada endpoint (parametros, forma real del
JSON de entrada/salida, codigos de error) y PROBARLO desde el propio
navegador ("Try it out"), sin fabricar peticiones a mano con curl o
Postman. El spec en si (OpenAPI 3.0, escrito a mano en
`api/openapi.json` para que describa el comportamiento REAL de cada
ruta, no algo generado por introspeccion) esta en
<http://127.0.0.1:5060/openapi.json> -- importable directamente en
Postman/Insomnia, o para generar un cliente automatico en otro
lenguaje.

**Mantenimiento:** `api/openapi.json` NO se regenera solo -- si se
añade/cambia un campo de request o response en `api/app.py` o
`src/predict.py`, hay que actualizar el spec a mano en el mismo commit
(el propio comentario de cabecera en `api/app.py`, junto al
registro de Swagger UI, lo recuerda).

## Despliegue para demos (sin hosting fijo todavia)

Punto #5 del roadmap: por ahora solo hace falta poder ENSEÑAR el API
funcionando (una entrevista, una entrega...), no tenerlo corriendo
24/7 -- la fase de despliegue real en un hosting de pago queda para
mas adelante. Con `data/processed/basketball.db` pesando ~284MB, la
mayoria de hostings gratuitos con disco efimero quedan descartados de
entrada para un despliegue permanente; para una demo puntual no hace
falta subir la base de datos a ningun sitio -- se sirve desde tu propio
equipo y se abre un tunel temporal hacia internet.

**Paso 1 -- levantar el API en modo produccion (sin el depurador de
Flask activo, IMPORTANTE antes de exponerlo a internet):**
```
pip install -r requirements.txt   # ya incluye waitress

set PRODUCTION=1                  (Windows cmd)
python api\app.py

$env:PRODUCTION=1                 (PowerShell)
python api\app.py
```
Sirve con `waitress` en <http://127.0.0.1:5060> (o el puerto que
marques con `PORT`). `debug=True` (el modo por defecto sin
`PRODUCTION=1`) deja activo el depurador interactivo de Werkzeug, que
permite ejecutar codigo arbitrario desde el navegador en cualquier
pagina de error -- inofensivo en localhost, un riesgo real en cuanto el
puerto se expone a internet con un tunel. Nunca uses el modo por
defecto para esto.

**Paso 2 -- abrir un tunel temporal hacia ese puerto.** Dos opciones,
ninguna sube la base de datos a ningun sitio (el tunel solo reenvia
peticiones HTTP a tu proceso local):
- **Cloudflare Quick Tunnel** (`cloudflared`) -- no requiere cuenta ni
  registro, un solo comando:
  ```
  cloudflared tunnel --url http://localhost:5060
  ```
  Imprime una URL publica `https://algo-aleatorio.trycloudflare.com`
  que reenvia a tu API mientras el comando siga corriendo.
- **ngrok** -- requiere crear una cuenta gratuita y configurar un
  authtoken una vez (`ngrok config add-authtoken ...`), luego:
  ```
  ngrok http 5060
  ```
  Tambien imprime una URL publica temporal (plan gratuito: cambia cada
  vez que reinicias el tunel).

**Paso 3 -- enseñarlo:** comparte la URL publica que te de el tunel
(p. ej. `https://algo.trycloudflare.com/health` para comprobar que
responde, o `https://algo.trycloudflare.com/predict_shot` con un
cliente HTTP) mientras dure la demo, y **cierra el tunel al terminar**
(Ctrl+C) -- mientras este abierto, tu API (de solo lectura, pero con
toda la base de datos detras) es alcanzable por cualquiera que tenga
la URL.

**Para cuando llegue el despliegue real (mas adelante, fuera de
alcance de esta fase):** el bloqueo principal sera el tamaño de
`basketball.db` (~284MB) -- las opciones son un hosting con disco
persistente de pago (Render/Railway/Fly.io, unos pocos euros/mes) o
recortar que fuentes viajan a produccion (p. ej. servir solo con las
fuentes de mas volumen/interes en vez de las 11 completas). No se ha
tomado ninguna decision todavia sobre esto -- pendiente de discutir
cuando toque.

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
    "model": "xgboost",
    "player_id": "203999"
  }
  ```
  `x_m`/`y_m` son coordenadas en **metros con origen en el aro** (misma
  convencion que `loc_x_m`/`loc_y_m` en `build_shot_features()`, NO las
  unidades nativas que guarda cada scraper en la tabla `shots` --
  decimas de pie en NBA, centimetros en Euroliga/EuroCup, porcentaje de
  cancha completa en FEB). `shot_type` es `"2"` o `"3"` para NBA/
  Euroliga/EuroCup/NextGen -- para las 7 competiciones FEB (ver mas
  abajo) tiene que ser `"desconocido"` en su lugar. `player_id`
  (**opcional**, añadido 2026-09-21, ver "Prior de jugador" mas abajo)
  es el id real de jugador de esa `source` (el mismo que devuelve
  `shots`/`players`) -- si se pasa, la prediccion usa el acierto
  historico REAL de ese jugador en la zona del tiro; si no se pasa, usa
  el acierto medio de liga de esa zona ("jugador medio"). Devuelve
  `make_probability`, `player_prior_fg` (el prior usado, de jugador o de
  liga) y `zone`/`dist_m`/`angle_deg` ya calculados con las mismas
  formulas que `src/features.py`.

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

  **Prior de jugador (añadido 2026-09-21):** antes de esto el modelo
  solo miraba la GEOMETRIA del tiro (distancia/angulo/zona/tipo/fuente)
  -- dos jugadores tirando desde el mismo sitio siempre daban la misma
  probabilidad, aunque uno fuera un especialista y el otro casi nunca
  tirara desde ahi. Se comprobo en datos reales (no se adivino) que hay
  señal real de jugador mas alla de la geometria: agrupando los 2.45M
  tiros por (source, player_id) con >=50 intentos, la desviacion real de
  FG% entre jugadores es ~4x la esperable solo por azar en la zona
  "aro" y ~2.3x en "triple_arco". Se añadieron `player_prior_fg`/
  `player_prior_n_log` -- el acierto/volumen PREVIO del jugador en esa
  zona, calculado solo con partidos anteriores (nunca fuga de datos,
  mismo criterio que el resto del proyecto) y encogido hacia el acierto
  de liga con 20 intentos virtuales (formula beta-binomial estandar,
  ver `add_shot_priors()` en `src/features.py`) -- con pocos tiros
  previos el prior queda cerca de la media de liga, con muchos se acerca
  al acierto real del jugador. En el API esto se calcula EN VIVO
  (`_player_zone_prior()` en `src/predict.py`) sobre el historico real
  de `shots` cuando se pasa `player_id`; sin `player_id` se usa
  directamente el acierto de liga guardado en la metadata del modelo
  (`league_zone_fg`), sin tocar la base de datos.

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
  triple de NBA, uno con `shot_type="desconocido"` de FEB, y uno con
  `player_id` real de NBA) mas peticiones mal formadas para comprobar
  los 400. Ejecutado de verdad en esta sesion (2026-09-21, tras añadir
  el prior de jugador -- ver mas abajo): `home_win_probability=0.4654`
  para Atlanta Hawks (local) - Boston Celtics (visitante); para tiro
  (modelo con 11 fuentes + prior de jugador, accuracy=0.633/
  AUC=0.658, sube desde 0.632/0.654 sin el prior), `make_probability=
  0.9764` junto al aro de NBA (jugador generico, `player_prior_fg=
  0.6563`, el FG% de liga de esa zona) vs `0.9782` para el mismo tiro
  con `player_id="1628969"` (Mikal Bridges, `player_prior_fg=0.7218`,
  su propio acierto real de aro es mayor que la media de liga -- el
  modelo SI reacciona al prior de jugador, no es un campo decorativo).
  El triple de referencia sale `0.0016` (muy de frente y justo pasada
  la linea) -- sigue pareciendo bajo frente a un ~30-35% esperado;
  seguramente refleja mas el AUC modesto del modelo en esa zona/angulo
  concreto que la realidad del baloncesto, una limitacion real (el
  modelo no tiene contexto de defensor/tirador especifico mas alla del
  prior de zona) no un bug del API. No se ha comprobado ningun numero
  contra una casa de apuestas ni fuente externa (no es esa la funcion
  de esta prueba).
- **Calibracion y limpieza de las 7 competiciones FEB** (sesion
  2026-09-20/21): la calibracion de coordenadas se valido con la
  distribucion real de distancias al aro (pico junto al aro, valle
  justo antes de la linea de 3, repunte despues -- patron real de
  cualquier grafico de tiro, ver comentario en `SHOT_COORD_CALIBRATION`
  de `src/features.py`). Tras la reingesta real del usuario, se
  verifico con `zone_fg_pct()` que el FG% por zona cae en rango
  conocido (aro 59-68%, triple_arco 27-35% en las 7 competiciones) antes
  de reentrenar `train_shot.py` con ellas.
