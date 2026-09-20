"""Logica de prediccion EN VIVO para el API REST (api/app.py) -- unico
modulo nuevo de logica real que trae esta fase (ver docstring de
api/app.py sobre la disciplina "rutas finas, logica en src/", ya
aplicada en app/app.py).

DIFERENCIA con features_pregame.py: ese modulo construye el dataset de
ENTRENAMIENTO -- una fila por partido YA JUGADO, con la media de cada
equipo hasta ANTES de ese partido concreto (shift(1).expanding().mean(),
ver su docstring). Aqui no hay un partido ya jugado que predecir: hay un
partido HIPOTETICO (dos equipos que igual ni se han enfrentado todavia),
asi que la pregunta es "que le pasaria a las medias pre-partido de este
equipo si jugara su proximo partido ahora mismo". La respuesta es la
media de TODOS los partidos que el equipo ya ha jugado -- exactamente lo
que devolveria un shift(1).expanding().mean() evaluado una fila mas
alla del ultimo partido real, sin necesidad de shiftear nada porque no
hay un partido "propio" que excluir.

Reutiliza build_team_game_features() (src/features.py) en vez de
recalcular nada desde team_stats -- misma disciplina de "una sola fuente
de verdad para las formulas" ya seguida por features_pregame.py.

rest_days: no hay fecha real de un partido que no se ha jugado, asi que
por defecto se asume el MISMO descanso para ambos equipos (diff=0) --
el caller puede afinarlo con home_rest_days/away_rest_days si de verdad
le importa esa feature (peso pequeño en el modelo entrenado, ver
coeficientes en train.py: diff_rest_days es la feature con menos peso
de las nueve, tanto en la regresion logistica como en XGBoost).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import joblib
import pandas as pd

from src.features import build_team_game_features
from src.features_pregame import ROLLING_STAT_COLS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"

# Mismo orden/nombres que FEATURE_COLS en train.py -- si un dia diverge
# de train.py hay que cambiar los dos a la vez (no hay una unica fuente
# de verdad automatica aqui porque train.py no expone su FEATURE_COLS
# como algo importable sin arrastrar sus dependencias de CLI).
FEATURE_COLS: list[str] = [f"diff_{c}_pre" for c in ROLLING_STAT_COLS] + ["diff_rest_days"]

_MODEL_FILES: dict[str, Path] = {
    "logreg": MODELS_DIR / "game_outcome_logreg.joblib",
    "xgboost": MODELS_DIR / "game_outcome_xgb.joblib",
}

_model_cache: dict[str, object] = {}

DEFAULT_MIN_PRIOR_GAMES = 5  # igual que --min-prior-games por defecto en train.py


class PredictionError(ValueError):
    """Error de datos de ENTRADA (equipo/fuente desconocidos, sin
    historial todavia, modelo no entrenado...) -- api/app.py lo traduce
    siempre a un 400/404, nunca a un 500: no es un fallo del servidor,
    es una peticion mal formada o prematura (p. ej. pedir una prediccion
    antes de haber ejecutado train.py, o de un equipo sin partidos
    jugados todavia esta temporada)."""


def available_models() -> list[str]:
    """Nombres de modelo cuyo .joblib existe ya en MODELS_DIR (puede
    estar vacio si no se ha ejecutado train.py todavia)."""
    return [name for name, path in _MODEL_FILES.items() if path.exists()]


def load_model(name: str):
    """Carga (y cachea en memoria de proceso) un modelo por nombre.
    Cache simple a proposito: la app la sirve un unico proceso Flask de
    desarrollo (ver docstring de api/app.py), no hace falta nada mas
    elaborado."""
    if name not in _MODEL_FILES:
        raise PredictionError(
            f"Modelo desconocido: {name!r}. Validos: {sorted(_MODEL_FILES)}"
        )
    if name not in _model_cache:
        path = _MODEL_FILES[name]
        if not path.exists():
            raise PredictionError(
                f"No existe {path.name} todavia -- ejecuta 'python train.py' "
                f"primero para entrenar y guardar los modelos."
            )
        _model_cache[name] = joblib.load(path)
    return _model_cache[name]


def load_metadata() -> dict:
    """Contenido de models/game_outcome_metadata.json (metricas reales
    del ultimo entrenamiento) -- ver GET /model/info en api/app.py."""
    path = MODELS_DIR / "game_outcome_metadata.json"
    if not path.exists():
        raise PredictionError(
            "No existe game_outcome_metadata.json todavia -- ejecuta "
            "'python train.py' primero."
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest_team_features(conn: sqlite3.Connection, source: str, team_id: str) -> dict:
    """Medias pre-partido ACTUALES de un equipo (ver docstring del
    modulo): media de pace_40/ortg/drtg/efg_pct/tov_pct/orb_pct/ft_rate/
    win sobre TODOS sus partidos jugados en su temporada mas reciente
    (dentro de `source`).

    Lanza PredictionError si el equipo no tiene ningun partido jugado
    todavia en una fuente entrenable (build_team_game_features() usa
    trainable_only=True por defecto, igual que el resto del proyecto) --
    no hay historial sobre el que calcular nada.
    """
    team_games = build_team_game_features(conn, sources=[source], trainable_only=True)
    if team_games.empty:
        raise PredictionError(
            f"Sin partidos jugados en source={source!r} (fuente desconocida, "
            f"no entrenable, o sin datos ingeridos todavia)."
        )
    team_games = team_games[team_games["team_id"] == team_id]
    if team_games.empty:
        raise PredictionError(
            f"team_id={team_id!r} no tiene ningun partido jugado en "
            f"source={source!r} -- revisa el identificador (GET /teams?"
            f"source={source} lista los equipos validos)."
        )

    latest_season = team_games["season"].max()
    season_games = team_games[team_games["season"] == latest_season]

    means = {col: float(season_games[col].mean()) for col in ROLLING_STAT_COLS}
    return {"season": latest_season, "n_games": int(len(season_games)), **means}


def predict_game(
    conn: sqlite3.Connection,
    source: str,
    home_team_id: str,
    away_team_id: str,
    model_name: str = "xgboost",
    home_rest_days: float = 0.0,
    away_rest_days: float = 0.0,
) -> dict:
    """Probabilidad de que gane el local en un partido HIPOTETICO
    (home_team_id/away_team_id no tienen por que haberse enfrentado
    nunca) segun el modelo `model_name` de train.py, usando las medias
    pre-partido ACTUALES de cada equipo (ver latest_team_features()).

    Lanza PredictionError (-> 400/404 en api/app.py) si algo de la
    entrada no es valido, en vez de dejar que reviente una excepcion
    interna de pandas/sklearn sin explicacion.
    """
    if home_team_id == away_team_id:
        raise PredictionError("home_team_id y away_team_id no pueden ser el mismo equipo.")

    home = latest_team_features(conn, source, home_team_id)
    away = latest_team_features(conn, source, away_team_id)

    row = {f"diff_{col}_pre": home[col] - away[col] for col in ROLLING_STAT_COLS}
    row["diff_rest_days"] = float(home_rest_days) - float(away_rest_days)
    X = pd.DataFrame([row], columns=FEATURE_COLS)

    model = load_model(model_name)
    home_win_prob = float(model.predict_proba(X)[0, 1])

    try:
        min_prior_games = int(load_metadata().get("min_prior_games", DEFAULT_MIN_PRIOR_GAMES))
    except PredictionError:
        min_prior_games = DEFAULT_MIN_PRIOR_GAMES

    warnings = []
    if home["n_games"] < min_prior_games:
        warnings.append(
            f"Local con solo {home['n_games']} partido(s) jugado(s) esta "
            f"temporada (< {min_prior_games}) -- media poco fiable, misma "
            f"exigencia que min_prior_games en train.py."
        )
    if away["n_games"] < min_prior_games:
        warnings.append(
            f"Visitante con solo {away['n_games']} partido(s) jugado(s) "
            f"esta temporada (< {min_prior_games}) -- media poco fiable."
        )

    return {
        "source": source,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "model": model_name,
        "home_win_probability": round(home_win_prob, 4),
        "away_win_probability": round(1.0 - home_win_prob, 4),
        "home_season": home["season"],
        "away_season": away["season"],
        "home_games_played": home["n_games"],
        "away_games_played": away["n_games"],
        "warnings": warnings,
    }
