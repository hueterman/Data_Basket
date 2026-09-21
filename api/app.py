"""API REST del proyecto (fase "despliegue de API REST con Flask") --
sirve el modelo de resultado de partido ya entrenado por train.py
(models/game_outcome_*.joblib) sobre datos YA guardados en
data/processed/basketball.db.

Misma disciplina que app/app.py (ver su docstring): toda la logica real
vive en src/ -- aqui en concreto src/predict.py -- y este fichero solo
define rutas Flask, valida el JSON/query de entrada y traduce
PredictionError a un 400/404. Usa la MISMA base de datos que la app de
exploracion y los scripts de ingesta (src.pipeline.db.get_connection()),
de SOLO LECTURA igual que app/app.py -- ninguna ruta escribe nada en
la base de datos.

Requiere haber ejecutado antes:
    python train.py
para que existan models/game_outcome_logreg.joblib,
models/game_outcome_xgb.joblib y models/game_outcome_metadata.json --
si no existen todavia, /predict y /model/info devuelven un error JSON
explicito (400/404), nunca un 500 silencioso (ver PredictionError en
src/predict.py).

Uso:
    pip install -r requirements.txt   # ya incluye flask
    python api/app.py
    -> escucha en http://127.0.0.1:5060

    Puerto configurable sin tocar el codigo con la variable de entorno
    PORT, igual que app/app.py:
        PORT=5061 python api/app.py            (Linux/Mac)
        set PORT=5061 && python api/app.py     (Windows cmd)
        $env:PORT=5061; python api/app.py      (PowerShell)

Endpoints:
    GET  /health
        {"status": "ok"} -- comprobacion basica de que el proceso responde.

    GET  /model/info
        Metadata real del ultimo entrenamiento (train.py): metricas de
        baseline/regresion logistica/XGBoost, feature_cols, min_prior_games,
        desglose por fuente... tal cual quedo guardado en
        models/game_outcome_metadata.json, mas "available_models" (que
        .joblib existen realmente en disco ahora mismo).

    GET  /teams?source=acb
        Lista {team_id, name} de los equipos de `source` que hay en la
        base de datos -- para saber que team_id pasarle a /predict sin
        tener que mirar la base de datos a mano.

    POST /predict
        Body JSON: {"source": "acb", "home_team_id": "...",
        "away_team_id": "...", "model": "xgboost" (opcional, default
        "xgboost"), "home_rest_days"/"away_rest_days" (opcionales,
        default 0 -- ver docstring de src/predict.py sobre por que).
        Devuelve home_win_probability/away_win_probability y avisos
        ("warnings") si algun equipo tiene poco historial esta
        temporada todavia.

    GET  /model/info/shot
        Igual que /model/info pero para el modelo de PROBABILIDAD DE
        TIRO (train_shot.py) -- metricas, "sources" (las fuentes con
        las que se entreno de verdad), sanity_check_by_zone...

    POST /predict_shot
        Body JSON: {"source": "nba", "x_m": 2.5, "y_m": 5.0,
        "shot_type": "2", "model": "xgboost" (opcional), "player_id":
        "203999" (opcional, AÑADIDO 2026-09-21)}. x_m/y_m son METROS con
        origen en el aro (no las unidades nativas de cada scraper -- ver
        docstring de src/predict.py:shot_make_probability()). SOLO
        fuentes con coordenadas de tiro calibradas Y con las que se
        entreno de verdad el modelo -- GET /model/info/shot dice cuales
        son ahora mismo (11 a fecha de esta version: nba, euroleague,
        eurocup, nextgen, feb, lfendesa, lfchallenge, lf2, segundafeb,
        tercerafeb, copaespaa). Para las 7 competiciones FEB, `shot_type`
        debe ser "desconocido" en vez de "2"/"3" -- su fuente no reporta
        el tipo real por tiro suelto (ver SOURCES_WITHOUT_REPORTED_SHOT_
        TYPE en src/predict.py). `player_id` (mismo id real que la
        columna player_id de `shots`/`players`, ver GET /teams o la app
        de exploracion): si se pasa, la prediccion usa el acierto
        historico REAL de ese jugador en la zona del tiro en vez del
        acierto medio de liga -- ver _player_zone_prior() en
        src/predict.py. Devuelve make_probability mas la zona/distancia/
        angulo ya calculados y player_prior_fg (el prior de jugador o de
        liga usado, segun se haya pasado player_id o no).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request

from src.pipeline.db import get_connection
from src.predict import (
    PredictionError,
    available_models,
    available_shot_models,
    load_metadata,
    load_shot_metadata,
    predict_game,
    shot_make_probability,
)

app = Flask(__name__)


def db():
    """Conexion nueva por peticion -- mismo criterio que app/app.py
    (SQLite + Flask de un solo proceso no necesita pool de conexiones
    para esto), y se beneficia del mismo modo WAL de get_connection()
    para no chocar con una ingesta escribiendo a la vez."""
    return get_connection()


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/model/info")
def model_info():
    """Metadata del modelo de RESULTADO DE PARTIDO (train.py)."""
    try:
        metadata = load_metadata()
    except PredictionError as exc:
        return jsonify({"error": str(exc)}), 404
    metadata["available_models"] = available_models()
    return jsonify(metadata)


@app.route("/model/info/shot")
def model_info_shot():
    """Metadata del modelo de PROBABILIDAD DE TIRO (train_shot.py) --
    ruta separada de /model/info porque son dos modelos con metricas y
    columnas de features completamente distintas, no dos versiones de
    lo mismo."""
    try:
        metadata = load_shot_metadata()
    except PredictionError as exc:
        return jsonify({"error": str(exc)}), 404
    metadata["available_models"] = available_shot_models()
    return jsonify(metadata)


@app.route("/teams")
def teams():
    source = request.args.get("source") or ""
    if not source:
        return jsonify({"error": "Falta el parametro de query 'source'."}), 400
    conn = db()
    try:
        rows = conn.execute(
            "SELECT team_id, name FROM teams WHERE source = ? ORDER BY name",
            (source,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify([{"team_id": r["team_id"], "name": r["name"]} for r in rows])


@app.route("/predict", methods=["POST"])
def predict():
    data = request.get_json(silent=True) or {}

    required = ("source", "home_team_id", "away_team_id")
    missing = [k for k in required if not data.get(k)]
    if missing:
        return jsonify({"error": f"Faltan campos obligatorios: {missing}"}), 400

    try:
        home_rest_days = float(data.get("home_rest_days", 0.0))
        away_rest_days = float(data.get("away_rest_days", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "home_rest_days/away_rest_days deben ser numericos."}), 400

    conn = db()
    try:
        result = predict_game(
            conn,
            source=data["source"],
            home_team_id=data["home_team_id"],
            away_team_id=data["away_team_id"],
            model_name=data.get("model", "xgboost"),
            home_rest_days=home_rest_days,
            away_rest_days=away_rest_days,
        )
    except PredictionError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()

    return jsonify(result)


@app.route("/predict_shot", methods=["POST"])
def predict_shot():
    data = request.get_json(silent=True) or {}

    required = ("source", "x_m", "y_m", "shot_type")
    missing = [k for k in required if data.get(k) is None]
    if missing:
        return jsonify({"error": f"Faltan campos obligatorios: {missing}"}), 400

    try:
        x_m = float(data["x_m"])
        y_m = float(data["y_m"])
    except (TypeError, ValueError):
        return jsonify({"error": "x_m/y_m deben ser numericos."}), 400

    player_id = data.get("player_id")
    conn = db() if player_id is not None else None
    try:
        result = shot_make_probability(
            source=data["source"],
            x_m=x_m,
            y_m=y_m,
            shot_type=str(data["shot_type"]),
            model_name=data.get("model", "xgboost"),
            conn=conn,
            player_id=str(player_id) if player_id is not None else None,
        )
    except PredictionError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        if conn is not None:
            conn.close()

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5060))
    app.run(debug=True, port=port)
