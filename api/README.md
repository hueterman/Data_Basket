# API REST del proyecto (Flask)

Sirve por HTTP el modelo de **resultado de partido** entrenado por
`train.py` (`models/game_outcome_logreg.joblib` /
`game_outcome_xgb.joblib`) sobre los datos ya guardados en
`data/processed/basketball.db`. Es de solo lectura, igual que
`app/app.py`: ninguna ruta escribe en la base de datos.

No incluye (todavia) el modelo de probabilidad de tiro de
`train_shot.py` -- se dejo fuera de esta primera version a proposito
(alcance acordado explicitamente antes de construirla) para no mezclar
dos contratos de API distintos en el primer paso; se puede añadir mas
adelante como un endpoint `/predict_shot` separado si hace falta.

## Arrancar

Requiere haber entrenado los modelos antes (si `models/` esta vacio,
`/predict` y `/model/info` devuelven un error JSON explicito en vez de
fallar en silencio):

```
python train.py
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
  existen `models/game_outcome_*.joblib` reales): cliente de pruebas de
  Flask (sin levantar el servidor) contra la base de datos real
  (`data/processed/basketball.db`) -- `GET /health`, `/model/info`,
  `/teams?source=nba` y un `POST /predict` real entre dos equipos NBA
  reales, mas una peticion mal formada para comprobar el 400. Ejecutado
  de verdad en esta sesion: `home_win_probability=0.4654` para un
  partido Atlanta Hawks (local) - Boston Celtics (visitante), numero
  razonable, no se ha comprobado contra ninguna casa de apuestas ni
  fuente externa (no es esa la funcion de esta prueba).
