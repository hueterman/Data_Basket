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
import math
import sqlite3
from pathlib import Path

import joblib
import pandas as pd

from src.features import SHOT_COORD_CALIBRATION, _classify_zone, build_team_game_features
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

# Segundo modelo (train_shot.py): P(made) de un tiro concreto -- mismas
# columnas/orden que FEATURE_COLS alli (dist_m/angle_deg/zone/shot_type/
# source). SOLO fuentes con coordenadas calibradas y verificadas en vivo
# (ver SHOT_COORD_CALIBRATION en src/features.py y la nota "AMPLIACION
# 2026-09-20" en train_shot.py sobre por que FEB queda fuera pese a
# tener tiros reales guardados).
SHOT_FEATURE_COLS: list[str] = ["dist_m", "angle_deg", "zone", "shot_type", "source"]

# Competiciones FEB (FebSource): su API no reporta el tipo de tiro (2/3)
# por tiro suelto, a diferencia de NBA/Euroliga/EuroCup/NextGen -- ver
# docstring de FebSource.get_shots() y de shot_make_probability() mas
# abajo. Se entrenan con shot_type="desconocido" fijo (train_shot.py),
# nunca con "2"/"3" para estas fuentes.
SOURCES_WITHOUT_REPORTED_SHOT_TYPE: frozenset[str] = frozenset({
    "feb", "lfendesa", "lfchallenge", "lf2", "segundafeb", "tercerafeb", "copaespaa",
})

_SHOT_MODEL_FILES: dict[str, Path] = {
    "logreg": MODELS_DIR / "shot_make_logreg.joblib",
    "xgboost": MODELS_DIR / "shot_make_xgb.joblib",
}
_shot_model_cache: dict[str, object] = {}

DEFAULT_MIN_PRIOR_GAMES = 5  # igual que --min-prior-games por defecto en train.py


class PredictionError(ValueError):
    """Error de datos de ENTRADA (equipo/fuente desconocidos, sin
    historial todavia, modelo no entrenado...) -- api/app.py lo traduce
    siempre a un 400/404, nunca a un 500: no es un fallo del servidor,
    es una peticion mal formada o prematura (p. ej. pedir una prediccion
    antes de haber ejecutado train.py, o de un equipo sin partidos
    jugados todavia esta temporada)."""


def _load_cached(cache: dict[str, object], name: str, files: dict[str, Path],
                  train_script: str) -> object:
    """Compartido por load_model()/load_shot_model() -- misma mecanica de
    cache de proceso (ver docstring de load_model()) para los dos
    modelos, cada uno con su propio diccionario de fichero/cache."""
    if name not in files:
        raise PredictionError(f"Modelo desconocido: {name!r}. Validos: {sorted(files)}")
    if name not in cache:
        path = files[name]
        if not path.exists():
            raise PredictionError(
                f"No existe {path.name} todavia -- ejecuta '{train_script}' primero."
            )
        cache[name] = joblib.load(path)
    return cache[name]


def _load_metadata_file(filename: str, train_script: str) -> dict:
    path = MODELS_DIR / filename
    if not path.exists():
        raise PredictionError(f"No existe {filename} todavia -- ejecuta '{train_script}' primero.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def available_models() -> list[str]:
    """Nombres de modelo de RESULTADO DE PARTIDO cuyo .joblib existe ya
    en MODELS_DIR (puede estar vacio si no se ha ejecutado train.py
    todavia)."""
    return [name for name, path in _MODEL_FILES.items() if path.exists()]


def load_model(name: str):
    """Carga (y cachea en memoria de proceso) un modelo de RESULTADO DE
    PARTIDO por nombre. Cache simple a proposito: la app la sirve un
    unico proceso Flask de desarrollo (ver docstring de api/app.py), no
    hace falta nada mas elaborado."""
    return _load_cached(_model_cache, name, _MODEL_FILES, "python train.py")


def load_metadata() -> dict:
    """Contenido de models/game_outcome_metadata.json (metricas reales
    del ultimo entrenamiento) -- ver GET /model/info en api/app.py."""
    return _load_metadata_file("game_outcome_metadata.json", "python train.py")


def available_shot_models() -> list[str]:
    """Nombres de modelo de PROBABILIDAD DE TIRO cuyo .joblib existe ya
    (puede estar vacio si no se ha ejecutado train_shot.py todavia)."""
    return [name for name, path in _SHOT_MODEL_FILES.items() if path.exists()]


def load_shot_model(name: str):
    """Carga (y cachea) un modelo de PROBABILIDAD DE TIRO por nombre --
    ver load_model() para el criterio de cache."""
    return _load_cached(_shot_model_cache, name, _SHOT_MODEL_FILES, "python train_shot.py")


def load_shot_metadata() -> dict:
    """Contenido de models/shot_make_metadata.json -- incluye "sources"
    (las fuentes con las que se entreno de verdad), usado por
    shot_make_probability() para rechazar una fuente que el modelo
    nunca vio en vez de dejar que OneHotEncoder(handle_unknown="ignore")
    la ignore en silencio."""
    return _load_metadata_file("shot_make_metadata.json", "python train_shot.py")


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


def shot_make_probability(
    source: str,
    x_m: float,
    y_m: float,
    shot_type: str,
    model_name: str = "xgboost",
) -> dict:
    """P(made) de un tiro HIPOTETICO, con el modelo de train_shot.py.

    x_m/y_m: coordenadas en METROS con origen en el aro -- misma
    convencion que loc_x_m/loc_y_m en build_shot_features() (src/
    features.py), NO las unidades nativas que guarda cada scraper en la
    tabla `shots` (decimas de pie en NBA, centimetros en Euroliga/
    EuroCup...) -- quien llame al API no tiene por que conocer esa
    tabla de conversion por fuente, ver SHOT_COORD_CALIBRATION.
    shot_type: "2" o "3" -- tipo de intento REAL, no derivado de zone
    (ver docstring de train_shot.py sobre por que se entrena con las
    dos columnas a la vez). Excepcion: las competiciones FEB (ver
    SOURCES_WITHOUT_REPORTED_SHOT_TYPE) exigen shot_type="desconocido"
    en su lugar -- su fuente no reporta un tipo real por tiro.

    Reutiliza _classify_zone() y los umbrales three_pt_*_m de
    SHOT_COORD_CALIBRATION (src/features.py) -- misma formula de zona
    que el dataset de entrenamiento, no una copia.

    Lanza PredictionError si `source` no tiene coordenadas calibradas,
    si shot_type no es "2"/"3", o si el modelo entrenado no vio esa
    fuente en su propio entrenamiento -- este ultimo caso NO fallaria
    con una excepcion de sklearn (OneHotEncoder(handle_unknown="ignore")
    simplemente la trataria como todo-ceros), pero seria una prediccion
    silenciosa sobre una categoria que el modelo nunca aprendio, asi que
    se rechaza explicitamente ("avisa y no adivina").
    """
    if source not in SHOT_COORD_CALIBRATION:
        raise PredictionError(
            f"Sin coordenadas de tiro calibradas para source={source!r}. "
            f"Fuentes calibradas: {sorted(SHOT_COORD_CALIBRATION)}"
        )
    if source in SOURCES_WITHOUT_REPORTED_SHOT_TYPE:
        if shot_type != "desconocido":
            raise PredictionError(
                f"source={source!r} no reporta el tipo de tiro (2/3) por tiro "
                f"suelto -- su API solo da coordenadas, no un campo equivalente "
                f"(ver docstring de FebSource.get_shots() en src/scraper/feb.py, "
                f"'BUGS REALES corregidos 2026-09-20(e)'). El modelo se entreno "
                f"con shot_type='desconocido' para esta fuente, nunca con '2'/'3' "
                f"-- pasa shot_type='desconocido' explicitamente en vez de "
                f"inventar un tipo que el modelo nunca vio en este contexto."
            )
    elif shot_type not in ("2", "3"):
        raise PredictionError(
            f"shot_type debe ser '2' o '3' para source={source!r} (recibido {shot_type!r})."
        )

    try:
        trained_sources = set(load_shot_metadata().get("sources", []))
    except PredictionError:
        trained_sources = set()
    if trained_sources and source not in trained_sources:
        raise PredictionError(
            f"El modelo de tiro no se entreno con source={source!r} "
            f"(entrenado con {sorted(trained_sources)}) -- se rechaza la "
            f"prediccion en vez de devolver un numero no fiable."
        )

    calib = SHOT_COORD_CALIBRATION[source]
    dist_m = math.hypot(x_m, y_m)
    angle_deg = math.degrees(math.atan2(abs(x_m), y_m))
    zone = _classify_zone(dist_m, x_m, calib["three_pt_arc_m"], calib["three_pt_corner_m"])

    row = {
        "dist_m": dist_m, "angle_deg": angle_deg, "zone": zone,
        "shot_type": shot_type, "source": source,
    }
    X = pd.DataFrame([row], columns=SHOT_FEATURE_COLS)

    model = load_shot_model(model_name)
    make_prob = float(model.predict_proba(X)[0, 1])

    return {
        "source": source,
        "x_m": x_m,
        "y_m": y_m,
        "dist_m": round(dist_m, 3),
        "angle_deg": round(angle_deg, 1),
        "zone": zone,
        "shot_type": shot_type,
        "model": model_name,
        "make_probability": round(make_prob, 4),
    }
