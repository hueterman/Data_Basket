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
    pip install -r requirements.txt   # ya incluye flask y waitress
    python api/app.py
    -> escucha en http://127.0.0.1:5051 (servidor de desarrollo de
       Flask, con el depurador interactivo activo -- SOLO localhost,
       nunca lo expongas a internet asi)

    Puerto configurable sin tocar el codigo con la variable de entorno
    PORT, igual que app/app.py:
        PORT=5061 python api/app.py            (Linux/Mac)
        set PORT=5061 && python api/app.py     (Windows cmd)
        $env:PORT=5061; python api/app.py      (PowerShell)

    PRODUCTION=1 sirve con waitress en vez del servidor de desarrollo
    (sin el depurador interactivo, seguro para exponer con un tunel
    tipo ngrok/cloudflared en una demo puntual) -- ver "Despliegue para
    demos" en api/README.md para el flujo completo:
        set PRODUCTION=1 && python api\app.py       (Windows cmd)
        $env:PRODUCTION=1; python api\app.py        (PowerShell)

Endpoints:
    GET  /docs
        Interfaz interactiva de Swagger UI (AÑADIDO 2026-09-21) -- prueba
        cada endpoint desde el navegador con formularios reales, en vez de
        fabricar peticiones a mano con curl/Postman. Lee de /openapi.json.

    GET  /openapi.json
        El spec OpenAPI 3.0 en si (api/openapi.json), por si se quiere
        importar en Postman/Insomnia o generar un cliente automaticamente.

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
        "xgboost"), "margin_model": "xgboost" (opcional, default
        "xgboost", AÑADIDO 2026-09-22 -- "ridge" o "xgboost", ver
        train_margin.py), "home_rest_days"/"away_rest_days" (opcionales,
        default 0 -- ver docstring de src/predict.py sobre por que).
        Devuelve home_win_probability/away_win_probability,
        home_used_season_carryover/away_used_season_carryover
        (AÑADIDO 2026-09-21 -- si la media pre-partido de ese equipo se
        encogio hacia su temporada anterior real, ver
        _add_season_carryover() en src/features_pregame.py), avisos
        ("warnings") solo cuando un equipo tiene poco historial esta
        temporada Y NINGUN arrastre de la temporada anterior que lo
        respalde, y predicted_margin/margin_model/margin_shap_explanation
        (AÑADIDO 2026-09-22, train_margin.py -- margen de victoria
        esperado en puntos, positivo = gana el local; None si
        train_margin.py no se ha ejecutado todavia, ver GET
        /model/info/margin).

    GET  /model/info/margin
        Igual que /model/info pero para el modelo de MARGEN DE VICTORIA
        (train_margin.py, AÑADIDO 2026-09-22) -- metricas (mae/rmse/r2/
        sign_accuracy de baseline/ridge/xgboost), feature_cols (misma
        lista que /model/info, distinto target).

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
        tercerafeb, copaespaa -- las 7 FEB estuvieron EXCLUIDAS un dia,
        2026-09-22, tras encontrar dos bugs reales en
        FebSource.get_shots() (team_id/player_id guardaban el
        nombre/dorsal en vez del id real), reingeridas y reincluidas el
        mismo dia, ver FEB_SHOTS_PENDING_REINGEST en train_shot.py).
        Para las 7 competiciones FEB, `shot_type` debe ser "desconocido"
        en vez de "2"/"3" -- su fuente no reporta el tipo real por tiro
        suelto (ver SOURCES_WITHOUT_REPORTED_SHOT_TYPE en
        src/predict.py).
        `player_id` (mismo id real que la columna player_id de
        `shots`/`players`, ver GET /teams o la app
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

from flask import Flask, jsonify, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_swagger_ui import get_swaggerui_blueprint

from src.pipeline.db import get_connection
from src.predict import (
    PredictionError,
    available_margin_models,
    available_models,
    available_shot_models,
    load_margin_metadata,
    load_metadata,
    load_shot_metadata,
    predict_game,
    shot_make_probability,
)

app = Flask(__name__)

# Rate limiting basico (AÑADIDO 2026-09-21) -- /predict y /predict_shot
# no tenian limite de peticiones ni autenticacion: aceptable mientras
# el API solo se enseña puntualmente detras de un tunel, pero si se
# deja accesible mas tiempo cualquiera podria saturarlo (a proposito o
# sin querer, p. ej. un script que reintenta en bucle). limiter.limit()
# se aplica solo a esas dos rutas (ver mas abajo) -- /health, /teams,
# /model/info* y la documentacion se quedan sin limite, son de lectura
# y mucho mas baratas de servir.
#
# Almacenamiento en memoria (por defecto de Flask-Limiter, sin
# storage_uri): correcto para un proceso Flask/waitress unico como
# este -- con varios procesos/workers cada uno llevaria su propia
# cuenta y el limite real seria mas alto que el nominal, pero este
# servicio no se despliega asi (ver "Despliegue para demos" en
# api/README.md).
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    headers_enabled=True,
)

# Documentacion OpenAPI/Swagger (AÑADIDO 2026-09-21, punto 6 del roadmap):
# api/openapi.json describe a mano los 6 endpoints reales (request/response
# de verdad, no generado por introspeccion de las rutas Flask -- misma
# disciplina "toda la logica real vive en src/, aqui solo rutas finas" no
# aplica a documentacion, pero se mantiene sincronizado a mano con este
# fichero, ver su comentario de cabecera). /openapi.json sirve el fichero
# tal cual; /docs monta la interfaz interactiva de Swagger UI (probar cada
# endpoint desde el navegador) apuntando a esa URL.
SWAGGER_URL = "/docs"
OPENAPI_URL = "/openapi.json"


@app.route(OPENAPI_URL)
def openapi_spec():
    return send_from_directory(Path(__file__).resolve().parent, "openapi.json")


app.register_blueprint(
    get_swaggerui_blueprint(SWAGGER_URL, OPENAPI_URL, config={"app_name": "Data Basket API"}),
    url_prefix=SWAGGER_URL,
)


def db():
    """Conexion nueva por peticion -- mismo criterio que app/app.py
    (SQLite + Flask de un solo proceso no necesita pool de conexiones
    para esto), y se beneficia del mismo modo WAL de get_connection()
    para no chocar con una ingesta escribiendo a la vez."""
    return get_connection()


@app.errorhandler(429)
def rate_limit_exceeded(exc):
    return jsonify({"error": f"Demasiadas peticiones -- limite: {exc.description}"}), 429


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


@app.route("/model/info/margin")
def model_info_margin():
    """Metadata del modelo de MARGEN DE VICTORIA (train_margin.py, AÑADIDO
    2026-09-22) -- ruta separada por el mismo motivo que /model/info/shot:
    metricas propias (mae/rmse/r2/sign_accuracy) que no tienen sentido
    mezcladas con las de /model/info."""
    try:
        metadata = load_margin_metadata()
    except PredictionError as exc:
        return jsonify({"error": str(exc)}), 404
    metadata["available_models"] = available_margin_models()
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
@limiter.limit("30 per minute")
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
            margin_model_name=data.get("margin_model", "xgboost"),
            home_rest_days=home_rest_days,
            away_rest_days=away_rest_days,
        )
    except PredictionError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()

    return jsonify(result)


@app.route("/predict_shot", methods=["POST"])
@limiter.limit("30 per minute")
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
    port = int(os.environ.get("PORT", 5051))  # cerca del 5050 de app/app.py
    # a proposito (CAMBIADO 2026-10-02, antes 5060): Pedro pidio acercarlo
    # al puerto del explorador de datos para no confundirlo con otra app
    # suya que ya ocupa el 5000 -- sigue siendo DISTINTO del 5050 real de
    # app/app.py (ver su docstring) para poder tener las dos corriendo a
    # la vez sin chocar.
    # PRODUCTION=1 (AÑADIDO 2026-09-21, ver "Despliegue para demos" en
    # api/README.md): sirve con waitress (servidor WSGI de verdad, puro
    # Python, funciona en Windows sin compilador a diferencia de
    # gunicorn) en vez del servidor de desarrollo de Flask. IMPORTANTE
    # por seguridad, no solo por rendimiento: app.run(debug=True) deja
    # activo el depurador interactivo de Werkzeug, que permite EJECUTAR
    # CODIGO ARBITRARIO desde el navegador en cualquier pagina de error
    # -- aceptable en localhost, un riesgo real en cuanto el puerto se
    # expone a internet (p. ej. con un tunel ngrok/cloudflared para
    # enseñar el API). Por defecto (sin PRODUCTION=1) el comportamiento
    # NO cambia -- sigue siendo el servidor de desarrollo con debug=True,
    # para no romper el flujo de trabajo local habitual.
    if os.environ.get("PRODUCTION") == "1":
        from waitress import serve
        print(f"Sirviendo con waitress (modo produccion/demo, SIN depurador) en "
              f"http://0.0.0.0:{port}")
        serve(app, host="0.0.0.0", port=port)
    else:
        app.run(debug=True, port=port)
