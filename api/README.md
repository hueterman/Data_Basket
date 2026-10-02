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

Escucha en <http://127.0.0.1:5051>. Puerto configurable sin tocar el
codigo con la variable de entorno `PORT` (p. ej. `PORT=5061 python
api/app.py`), igual que `app/app.py`.

## Documentacion interactiva (Swagger UI)

Con el servidor arrancado, <http://127.0.0.1:5051/docs/> abre una
interfaz donde se puede ver cada endpoint (parametros, forma real del
JSON de entrada/salida, codigos de error) y PROBARLO desde el propio
navegador ("Try it out"), sin fabricar peticiones a mano con curl o
Postman. El spec en si (OpenAPI 3.0, escrito a mano en
`api/openapi.json` para que describa el comportamiento REAL de cada
ruta, no algo generado por introspeccion) esta en
<http://127.0.0.1:5051/openapi.json> -- importable directamente en
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
Sirve con `waitress` en <http://127.0.0.1:5051> (o el puerto que
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
  cloudflared tunnel --url http://localhost:5051
  ```
  Imprime una URL publica `https://algo-aleatorio.trycloudflare.com`
  que reenvia a tu API mientras el comando siga corriendo.
- **ngrok** -- requiere crear una cuenta gratuita y configurar un
  authtoken una vez (`ngrok config add-authtoken ...`), luego:
  ```
  ngrok http 5051
  ```
  Tambien imprime una URL publica temporal (plan gratuito: cambia cada
  vez que reinicias el tunel).

**Paso 3 -- enseñarlo:** comparte la URL publica que te de el tunel
(p. ej. `https://algo.trycloudflare.com/health` para comprobar que
responde, o `https://algo.trycloudflare.com/predict_shot` con un
cliente HTTP) mientras dure la demo, y **cierra el tunel al terminar**
(Ctrl+C) -- mientras este abierto, tu API (de solo lectura, pero con
toda la base de datos detras) es alcanzable por cualquiera que tenga
la URL. `/predict` y `/predict_shot` (AÑADIDO 2026-09-21) tienen un
rate limit basico de 30 peticiones/minuto por IP (Flask-Limiter, ver
`api/app.py`) para que nadie las sature sin querer mientras el tunel
esta abierto -- sigue sin haber autenticacion, asi que esto no
sustituye a cerrar el tunel al terminar.

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
    "margin_model": "xgboost",
    "home_rest_days": 0,
    "away_rest_days": 0
  }
  ```
  `source`/`home_team_id`/`away_team_id` son obligatorios; el resto son
  opcionales (`model` por defecto `"xgboost"`, el otro valor valido es
  `"logreg"`; `margin_model` por defecto `"xgboost"`, el otro valor
  valido es `"ridge"` -- ver "Margen de victoria" mas abajo). Devuelve
  `home_win_probability`/`away_win_probability` mas un array `warnings`
  (p. ej. si algun equipo lleva pocos partidos jugados esta temporada
  todavia, misma exigencia -- `min_prior_games`, 5 por defecto -- que
  usa `train.py` para construir el dataset de entrenamiento),
  `home_elo`/`away_elo` (ver "Rating ELO opponent-adjusted" mas abajo),
  `shap_explanation` (ver "Explicabilidad (SHAP)" mas abajo) y
  (AÑADIDO 2026-09-22) `predicted_margin`/`margin_model`/
  `margin_shap_explanation` -- margen de victoria esperado en puntos
  (positivo = gana el local), `null` si `train_margin.py` no se ha
  ejecutado todavia (ver "Margen de victoria" mas abajo). Un
  `margin_model` con un nombre que no existe (p. ej. un typo) devuelve
  400, igual que un `model` invalido -- solo el caso "nombre valido
  pero sin entrenar" deja el campo a `null` en vez de fallar.
- `GET /model/info/shot` -- igual que `/model/info` pero para el modelo
  de tiro: metricas, `sources` (las fuentes con las que se entreno de
  verdad -- ahora mismo NBA, Euroliga, EuroCup y NextGen, ver mas
  abajo), `sanity_check_by_zone`...
- `GET /model/info/margin` -- (AÑADIDO 2026-09-22) igual que
  `/model/info` pero para el modelo de MARGEN DE VICTORIA
  (`train_margin.py`): `mae`/`rmse`/`r2`/`sign_accuracy` de
  baseline/ridge/xgboost, `feature_cols` (identico a `/model/info`,
  distinto target), `ridge_metrics_by_source`, `available_models`.
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
  formulas que `src/features.py`, mas `shap_explanation` (ver
  "Explicabilidad (SHAP)" mas abajo).

  **Solo fuentes con coordenadas de tiro CALIBRADAS y con las que el
  modelo se entreno de verdad** (`GET /model/info/shot` -> `sources`
  dice cuales exactamente): a fecha de esta version, 11 -- `nba`,
  `euroleague`, `eurocup`, `nextgen` y las 7 competiciones FEB (`feb`,
  `lfendesa`, `lfchallenge`, `lf2`, `segundafeb`, `tercerafeb`,
  `copaespaa`).

  Las 7 FEB tuvieron su propio capitulo, en tres rondas:

  1. (2026-09-20/21) Al calibrarlas se descubrieron dos bugs reales en
     `FebSource.get_shots()` (`src/scraper/feb.py`) -- `made` salia
     SIEMPRE `True` (`bool("0")` es `True` en Python, "m" es un string)
     y `shot_type` guardaba el reloj del partido, no el tipo de tiro
     (la API de FEB no reporta eso por tiro suelto, a diferencia de
     NBA/Euroliga). Corregido, verificado en vivo contra un boxscore
     real (FGM/FGA exactos), y reingerido de verdad por el usuario
     (`ingest_historical.py --no-cache`, necesario porque la cache de
     `data/raw/` guardaba el dato ya mal parseado) antes de reentrenar
     -- ver el aviso "REINGESTA CONFIRMADA 2026-09-21" en
     `train_shot.py`. Como su API no reporta un tipo de tiro real por
     tiro suelto, se entrenaron con `shot_type="desconocido"` fijo, y
     `/predict_shot` rechaza con un 400 cualquier `"2"`/`"3"` para
     estas 7 fuentes en vez de dejar que el modelo generalice sobre una
     categoria que nunca vio en ese contexto (ver
     `SOURCES_WITHOUT_REPORTED_SHOT_TYPE` en `src/predict.py`).
  2. (2026-09-22) Al retomar el bug ya conocido de `shots.team_id`
     (guardaba el NOMBRE del equipo en vez de su ID numerico real, sin
     afectar todavia a ninguna feature en uso) se verifico en vivo por
     primera vez un payload REAL completo del ShotChart API (navegador
     integrado, `fetch()` autenticado contra game_id=2220021, 120/120
     tiros comprobados) y aparecio un SEGUNDO bug, mas grave: `player`
     en cada tiro NO es el id real del jugador -- es su DORSAL (el
     mismo dorsal se repite entre equipos, confirmado con un caso real:
     "no"="22" son dos jugadores distintos en los dos equipos del
     partido de prueba). Esto SI afectaba a una feature en produccion:
     `add_shot_priors()` agrupa por `(source, player_id)`, asi que el
     prior de jugador de estas 7 fuentes mezclaba el historial de
     jugadores DISTINTOS que compartieron dorsal en partidos distintos.
     El payload SI trae los IDs reales (`TEAM[i].id`, el mismo de
     `Equipo.aspx?i=`; `TEAM[i].PLAYER[].id`, el mismo de
     `Jugador.aspx?i={team_id}&c={CODIGO}`) -- no habia que inventar
     nada, solo leerlos (ver "BUGS REALES corregidos 2026-09-22" en
     `FebSource.get_shots()`). Codigo corregido y verificado con tests,
     pero las filas de `shots` YA guardadas para estas 7 fuentes seguian
     con el bug (misma razon que la ronda anterior: la cache de
     `data/raw/` guarda el Shot ya parseado) -- excluidas de nuevo de
     `train_shot.py` hasta reingerir de verdad.
  3. (2026-09-22, mismo dia) El usuario reingirio de verdad las 7 con
     `ingest_historical.py --no-cache` (~630k tiros, cada uno requirio
     golpear la API en vivo de FEB de nuevo). Verificado contra la base
     de datos real tras la reingesta: `shots.team_id` de las 7 fuentes
     matchea AL 100% contra `teams.team_id` real (186986/186986 para
     "feb", primera union real posible), `shots.player_id` ya resuelve
     a codigos reales de `players.player_id` (no el 100% -- `players`
     solo guarda la plantilla ACTUAL de cada equipo via
     `list_players()`, no el historico completo de 5 temporadas, asi
     que jugadores de temporadas pasadas que ya no estan en el equipo
     no aparecen alli, algo esperado y no un bug de este fix), made%
     36.3-44.6% por fuente y FG% por zona (`zone_fg_pct()`, 629878
     tiros utilizables) de nuevo en rango real conocido (aro 64.1%,
     triple_arco 31.8%). Reentrenado `train_shot.py` con las 11 fuentes
     -- ver "REINGESTA CONFIRMADA 2026-09-22" en `train_shot.py`.

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

**Explicabilidad (SHAP) (añadido 2026-09-21):** tanto `/predict` como
  `/predict_shot` devuelven ahora `shap_explanation`, un desglose de
  QUE feature peso mas en esa prediccion CONCRETA (no en el modelo en
  general) -- pensado para enseñar el modelo en clase, no solo su
  resultado. Forma:
  ```json
  {
    "base_value": 0.5123,
    "contributions": [
      {"feature": "diff_efg", "shap_value": 0.0842},
      {"feature": "diff_rest_days", "shap_value": -0.0031}
    ]
  }
  ```
  `base_value` es la probabilidad "de partida" (sin mirar ninguna
  feature de este partido concreto) y cada `shap_value` es cuanto
  empuja esa feature la probabilidad hacia arriba (positivo) o hacia
  abajo (negativo) respecto a `base_value`, en el mismo espacio que
  `home_win_probability`/`make_probability` (NO log-odds; para los
  modelos de margen, AÑADIDO 2026-09-22, la escala nativa es puntos, no
  probabilidad, pero la aditividad es la misma) --
  `base_value + suma(shap_value de todas las features) ==
  home_win_probability` (o `make_probability`, o `predicted_margin`)
  exactamente, verificado contra los 6 modelos reales (diferencia
  0.000000). `contributions`
  viene ordenado de mayor a menor peso absoluto, y las columnas
  categoricas de `/predict_shot` (`zone`/`shot_type`/`source`, que
  internamente se expanden en varias columnas one-hot dentro del
  Pipeline) se agregan de vuelta a la feature original antes de
  devolverse, para que la lista siempre tenga una entrada por cada
  feature de `FEATURE_COLS`/`SHOT_FEATURE_COLS`, nunca por cada dummy.

  Implementacion: `shap.Explainer(modelo.predict_proba, background)`
  para los clasificadores (resultado de partido/tiro) o
  `shap.Explainer(modelo.predict, background)` para los regresores de
  margen (Ridge/XGBRegressor no tienen `predict_proba`, AÑADIDO
  2026-09-22 -- ver `_shap_contributions()` en `src/predict.py`) en vez
  de `TreeExplainer`, porque `TreeExplainer` falla en real contra
  `game_outcome_xgb.joblib`/`shot_make_xgb.joblib`/`game_margin_xgb.joblib`
  -- XGBoost 3.x serializa `base_score` como un string tipo array
  (`"[5.8281195E-1]"`) que el parser nativo de `shap` para modelos
  XGBoost no sabe leer (`ValueError: could not convert string to
  float`), y no es corregible por configuracion porque la API interna
  de XGBoost siempre serializa asi. La API generica evita ese camino
  por completo y funciona igual para regresion logistica, XGBoost y
  Ridge. Coste: ~3.5s de calentamiento JIT la primera vez que se llama
  en el proceso (import + compilacion), luego ~1.5ms por peticion -- no
  hace falta cache aparte, ya vive dentro del mismo proceso largo del
  servidor.

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
`rest_days` (la feature con menos peso de las diez del modelo, ver
coeficientes/importancias que imprime `train.py`) se asume igual para
ambos equipos por defecto (`diff_rest_days = 0`) -- se puede afinar con
`home_rest_days`/`away_rest_days` en el body si interesa.

**Rating ELO opponent-adjusted (añadido 2026-09-21):** ademas de la
media de estadisticas pre-partido de cada equipo, el modelo usa
`diff_elo_pre` -- un rating ELO (base 1500, actualizado partido a
partido con la formula estandar mas un multiplicador de margen de
victoria adaptado de la metodologia ELO de NBA de FiveThirtyEight) que
SI ajusta por la calidad de TODOS los rivales a los que se ha
enfrentado un equipo, no solo su propio % de victorias -- un 8-2 contra
rivales flojos y un 8-2 contra los mejores del grupo ya no se ven
igual. Se probaron antes dos alternativas mas simples (prior de
enfrentamiento directo con el mismo encogido bayesiano que el prior de
jugador de tiro, y una "fuerza de calendario" de un solo salto) y
NINGUNA de las dos aporto señal real en el split cronologico de
train.py (roc_auc practicamente identico, incluso un pelin peor en el
segundo caso) -- el ELO, al ajustar de forma ITERATIVA (la fuerza de un
rival depende de a quien ha ganado el, encadenado sobre todo el
historial), si la aporta: roc_auc 0.7406 -> 0.7481, log_loss 0.5901 ->
0.5835, brier 0.2029 -> 0.1999 en el mismo split, y es la feature con
MAS peso del modelo (coeficiente mas alto en la regresion logistica,
gain dominante en XGBoost) -- ver el bloque "Rating ELO
opponent-adjusted" en `src/features_pregame.py` para el detalle
completo (formula, verificacion, limitaciones conocidas). `home_elo`/
`away_elo` en la respuesta son el rating ACTUAL (a dia de hoy) de cada
equipo, calculado en vivo reproduciendo todo el historial de la fuente
(`_current_elo_ratings()` en `src/predict.py`) -- 1500 significa "sin
historial todavia".

**Arrastre entre temporadas (añadido 2026-09-21):** la media pre-partido
de cada equipo ya NO es solo de su temporada mas reciente -- si el
equipo jugo una temporada anterior real (>= 10 partidos) en la misma
`source`, se encoge hacia esa media anterior con 10 partidos "virtuales"
de peso (formula beta-binomial, mismo patron que el prior de jugador del
modelo de tiro, ver `SEASON_PRIOR_K_SHRINK`/`_add_season_carryover()` en
`src/features_pregame.py`). Antes de esto, un equipo con pocos partidos
en su temporada actual daba una prediccion basada en una media de 1-2
partidos (ruido); ahora se apoya en la temporada anterior completa
mientras la actual todavia es corta, y el peso se desplaza solo hacia la
temporada actual a medida que se juegan mas partidos. La respuesta
incluye `home_used_season_carryover`/`away_used_season_carryover` para
saber si un equipo concreto se beneficio de esto. Verificado en datos
reales antes de implementarlo: la correlacion entre el rendimiento de
una temporada y la siguiente del mismo equipo es alta en las 8 columnas
(0.52-0.94 segun la metrica, 461 pares reales de temporadas
consecutivas) -- no es una suposicion.

**Margen de victoria (añadido 2026-09-22):** ademas de la probabilidad
de victoria, `/predict` devuelve el margen esperado en puntos
(`predicted_margin`, positivo = gana el local) usando un segundo modelo
independiente entrenado por `train_margin.py` -- MISMAS features que el
modelo de resultado (`diff_*_pre` + `diff_rest_days` + `diff_elo_pre`),
solo cambia el target (continuo, no binario) y el tipo de modelo
(regresion). Dos opciones (`margin_model`, por defecto `"xgboost"`):
Ridge (regularizado con L2 en vez de OLS simple -- `diff_elo_pre` y
`diff_win_pre` estan correlacionados de verdad, 0.887, y OLS sin
regularizar seria inestable con esa colinealidad) y XGBoost (mismos
hiperparametros conservadores que el modelo de resultado). Verificado
en el split cronologico real de `train_margin.py` (18180 partidos
utilizables): ambos baten claramente al baseline (predecir siempre la
media de margen observada) -- MAE 10.6-10.7 puntos frente a 12.4 del
baseline, R²≈0.26, y un `sign_accuracy` (el signo del margen predicho
coincide con quien gana de verdad) de ~0.69, comparable al accuracy del
propio modelo de clasificacion en el mismo periodo -- comprobacion de
cordura de que el modelo de margen no es peor "adivinando quien gana"
que el modelo pensado especificamente para eso. Como con el modelo de
resultado, si `train_margin.py` no se ha ejecutado todavia
`predicted_margin`/`margin_model`/`margin_shap_explanation` salen a
`null` en vez de romper `/predict` entero -- es un modelo secundario,
no bloqueante.

## Validado

- **Mecanica** (`tests/test_predict_game_outcome.py`, `tests/test_predict_shot.py`
  y `tests/test_feb_shots_parsing.py`, migrados desde el antiguo
  `scripts/test_api.py`/`scripts/test_feb_shots_fix.py` a una suite
  pytest real -- `pytest tests/` desde la raiz del proyecto, tambien se
  ejecuta sola en GitHub Actions en cada push, ver
  `.github/workflows/tests.yml`): base de datos SQLite de prueba
  fabricada a mano (4 equipos, 22 partidos sinteticos, NO datos reales
  de ninguna fuente) + un modelo de mentira solo para probar la forma de
  entrada/salida de `predict_proba` -- comprueba que las medias
  pre-partido salen correctas, que el aviso de "poco historial" aparece
  cuando corresponde (y se suprime con arrastre entre temporadas), y que
  los casos de error esperados (mismo equipo local/visitante, equipo
  inexistente, fuente sin datos, modelo desconocido, y sus equivalentes
  para el modelo de tiro) lanzan `PredictionError` en vez de reventar.
- **Mecanica del modelo de margen** (`tests/test_predict_margin.py`,
  AÑADIDO 2026-09-22): misma base de datos sintetica de
  `test_predict_game_outcome.py` (via su fixture `game_outcome_env`,
  reutilizada) + modelos de mentira propios (un Pipeline
  StandardScaler+Ridge y un XGBRegressor bareo, mismas clases reales que
  `train_margin.py`) -- comprueba que `predicted_margin`/`margin_model`/
  `margin_shap_explanation` salen `None` cuando el modelo de margen
  todavia no esta entrenado (sin romper `/predict`), que un
  `margin_model` invalido SI lanza `PredictionError` (no se confunde con
  "no entrenado todavia"), y la aditividad de Shapley del SHAP de
  margen.
- **Integracion real** (`tests/test_api_integration.py`, SOLO si ya
  existen los `.joblib`/`basketball.db` reales -- si no existen, cada
  test se salta con `pytest.skip` en vez de fallar, para que la misma
  suite funcione en un checkout limpio de CI sin esos ficheros
  gitignored): cliente de pruebas de
  Flask (sin levantar el servidor) contra la base de datos real
  (`data/processed/basketball.db`) -- `GET /health`, `/model/info`,
  `/teams?source=nba`, un `POST /predict` real entre dos equipos NBA
  reales, `GET /model/info/shot`, `POST /predict_shot` real (aro y
  triple de NBA, uno con `shot_type="desconocido"` de FEB, y uno con
  `player_id` real de NBA) mas peticiones mal formadas para comprobar
  los 400. Ejecutado de verdad en esta sesion (2026-09-21, tras añadir
  el arrastre entre temporadas y el prior de jugador -- ver mas abajo):
  `home_win_probability=0.4663` para Atlanta Hawks (local) - Boston
  Celtics (visitante), ambos equipos con `used_season_carryover=True`
  (temporada 2025-26 completa, 82 partidos cada uno -- el arrastre pesa
  poco a estas alturas de temporada, pero sigue aplicandose); modelo de
  resultado de partido con el arrastre, accuracy=0.683/AUC=0.735 (sube
  desde 0.682/0.731 sin el, y con 839 partidos mas utilizables -- los
  que antes se descartaban por tener pocos partidos AUN con temporada
  anterior real detras). Para tiro (modelo con 11 fuentes + prior de
  jugador, accuracy=0.633/AUC=0.658, sube desde 0.632/0.654 sin el
  prior), `make_probability=0.9764` junto al aro de NBA (jugador
  generico, `player_prior_fg=0.6563`, el FG% de liga de esa zona) vs
  `0.9782` para el mismo tiro con `player_id="1628969"` (Mikal Bridges,
  `player_prior_fg=0.7218`, su propio acierto real de aro es mayor que
  la media de liga -- el modelo SI reacciona al prior de jugador, no es
  un campo decorativo).
  El triple de referencia sale `0.0016` (muy de frente y justo pasada
  la linea) -- sigue pareciendo bajo frente a un ~30-35% esperado;
  seguramente refleja mas el AUC modesto del modelo en esa zona/angulo
  concreto que la realidad del baloncesto, una limitacion real (el
  modelo no tiene contexto de defensor/tirador especifico mas alla del
  prior de zona) no un bug del API. No se ha comprobado ningun numero
  contra una casa de apuestas ni fuente externa (no es esa la funcion
  de esta prueba).
  **Nota 2026-09-22:** entre el registro de arriba (2026-09-21) y esta
  nota, las 7 FEB pasaron por una excursion completa -- excluidas tras
  encontrar los bugs de `team_id`/`player_id` (ver "Solo fuentes con
  coordenadas de tiro CALIBRADAS..." mas arriba), reingeridas de verdad
  por el usuario el mismo dia, verificadas contra la base de datos real
  y reincluidas -- el modelo real vuelve a tener las 11 fuentes de
  entonces, ya sin el bug detras.
  `GET /model/info/margin` y `predicted_margin`/`margin_model`/
  `margin_shap_explanation` en `/predict` (AÑADIDO 2026-09-22, tambien
  ejecutado de verdad contra `data/processed/basketball.db` real):
  mismo partido Atlanta Hawks (local) - Boston Celtics (visitante) de
  arriba da `predicted_margin=-2.1` con `margin_model="xgboost"` y
  `-2.0` con `"ridge"` -- ambos NEGATIVOS (favorecen al visitante),
  consistente con `home_win_probability=0.445` (< 0.5) del mismo
  partido: los dos modelos, entrenados por separado con targets
  distintos, coinciden en direccion. `margin_shap_explanation` aditivo
  verificado igual que el del modelo de resultado.
- **Calibracion y limpieza de las 7 competiciones FEB** (sesion
  2026-09-20/21): la calibracion de coordenadas se valido con la
  distribucion real de distancias al aro (pico junto al aro, valle
  justo antes de la linea de 3, repunte despues -- patron real de
  cualquier grafico de tiro, ver comentario en `SHOT_COORD_CALIBRATION`
  de `src/features.py`). Tras la reingesta real del usuario, se
  verifico con `zone_fg_pct()` que el FG% por zona cae en rango
  conocido (aro 59-68%, triple_arco 27-35% en las 7 competiciones) antes
  de reentrenar `train_shot.py` con ellas.
- **Bug de `team_id`/`player_id` en `FebSource.get_shots()`** (sesion
  2026-09-22, retomado tras dejarlo pendiente en una sesion anterior):
  verificado en vivo por primera vez un payload REAL completo del
  ShotChart API (navegador integrado, `fetch()` autenticado, 120/120
  tiros de game_id=2220021 comprobados uno a uno contra el roster real
  de cada equipo) -- `shots.team_id` guardaba el nombre del equipo
  (`team["name"]`) en vez de su id numerico real (`team["id"]`, SI
  presente en el payload, identico al de `Equipo.aspx?i=`), y
  `shots.player_id` guardaba el DORSAL del tiro (`shot["player"]`) en
  vez del id real del jugador (`team["PLAYER"][].id`, resuelto contra
  `team["PLAYER"][].no`) -- confirmado con un caso real de dorsal
  duplicado entre los dos equipos del partido de prueba (dos jugadores
  DISTINTOS, mismo dorsal "22"). El segundo bug corrompia
  `player_prior_fg` (agrupa por `(source, player_id)`, ver
  `add_shot_priors()` en `src/features.py`) para las 7 fuentes FEB.
  Corregido en `FebSource.get_shots()`, con tests offline contra el
  payload real (`tests/test_feb_shots_parsing.py`, incluye el caso de
  dorsal duplicado y el de dorsal sin match en el roster). Las filas de
  `shots` YA guardadas seguian con el bug (misma razon que la ronda
  anterior de bugs, la cache de `data/raw/` guarda el Shot ya parseado)
  -- `train_shot.py` excluyo de nuevo las 7 fuentes
  (`FEB_SHOTS_PENDING_REINGEST`) y se reentreno con solo
  nba/euroleague/eurocup/nextgen para que el modelo en produccion
  dejara de usar datos con el bug mientras tanto.

  **Reingesta completada el mismo dia**: el usuario ejecuto
  `ingest_historical.py --no-cache` sobre las 7 fuentes. Verificado
  contra la base de datos real tras la reingesta: `shots.team_id`
  matchea AL 100% contra `teams.team_id` real (186986/186986 para
  "feb"), `shots.player_id` ya resuelve a codigos reales de
  `players.player_id`, made% 36.3-44.6% por fuente y FG% por zona
  (629878 tiros utilizables entre las 7) de nuevo en rango real
  conocido (aro 64.1%, triple_arco 31.8%). `FEB_SHOTS_PENDING_REINGEST`
  se vacio de nuevo (ver "REINGESTA CONFIRMADA 2026-09-22" en
  `train_shot.py`) y se reentreno con las 11 fuentes
  (accuracy=0.634/AUC=0.658).
