"""Mecanica del modelo P(made) de tiro (api/app.py + src/predict.py).

Migrado desde scripts/test_api.py (parte 1b, "Mecanica del modelo de
TIRO") a pytest -- modelo/metadata de mentira en un MODELS_DIR temporal,
sin depender de haber entrenado nada de verdad ni de red.
"""
from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.pipeline.db import get_connection


def _fit_dummy_shot_model():
    """Modelo de mentira para P(made) -- mismo tipo de pipeline que
    train_shot.py (ColumnTransformer con StandardScaler + OneHotEncoder,
    LogisticRegression encima) para que predict_proba() acepte
    exactamente las mismas columnas/tipos que el real, sin depender de
    haber entrenado nada de verdad."""
    rng = np.random.default_rng(1)
    n = 60
    X = pd.DataFrame({
        "dist_m": rng.uniform(0, 8, n),
        "angle_deg": rng.uniform(0, 90, n),
        # player_prior_fg/player_prior_n_log -- ver add_shot_priors() en
        # src/features.py y _player_zone_prior() en src/predict.py.
        # Mismas columnas que SHOT_FEATURE_COLS real.
        "player_prior_fg": rng.uniform(0.2, 0.7, n),
        "player_prior_n_log": rng.uniform(0.0, 5.0, n),
        "zone": rng.choice(["aro", "media_larga", "triple_arco"], n),
        "shot_type": rng.choice(["2", "3"], n),
        "source": rng.choice(["nba", "euroleague"], n),
    })
    y = (X["dist_m"] < 3).astype(int).to_numpy()
    pipe = Pipeline([
        ("pre", ColumnTransformer([
            ("num", StandardScaler(), ["dist_m", "angle_deg", "player_prior_fg", "player_prior_n_log"]),
            ("cat", OneHotEncoder(handle_unknown="ignore"), ["zone", "shot_type", "source"]),
        ])),
        ("clf", LogisticRegression()),
    ])
    pipe.fit(X, y)
    return pipe


@pytest.fixture()
def shot_env(tmp_path):
    """Modelo + metadata de mentira en un MODELS_DIR temporal. Limita
    "sources" a nba/euroleague/feb, para poder probar el rechazo de una
    fuente calibrada pero NO entrenada (eurocup SI esta calibrada en
    SHOT_COORD_CALIBRATION pero no aparece aqui a proposito)."""
    import src.predict as predict_mod
    predict_mod.MODELS_DIR = tmp_path
    predict_mod._SHOT_MODEL_FILES = {
        "logreg": tmp_path / "shot_make_logreg.joblib",
        "xgboost": tmp_path / "shot_make_xgb.joblib",
    }
    predict_mod._shot_model_cache.clear()
    predict_mod._metadata_cache.clear()
    model = _fit_dummy_shot_model()
    joblib.dump(model, predict_mod._SHOT_MODEL_FILES["logreg"])
    joblib.dump(model, predict_mod._SHOT_MODEL_FILES["xgboost"])
    with open(tmp_path / "shot_make_metadata.json", "w", encoding="utf-8") as f:
        json.dump({
            "sources": ["nba", "euroleague", "feb"],
            "league_zone_fg": [
                {"source": "nba", "zone": "aro", "fg_pct": 0.65},
                {"source": "feb", "zone": "aro", "fg_pct": 0.60},
            ],
            "shot_prior_k_shrink": 20.0,
        }, f)
    return predict_mod


def test_rim_shot_classified_and_probability_bounded(shot_env):
    result = shot_env.shot_make_probability(
        source="nba", x_m=0.5, y_m=1.0, shot_type="2", model_name="logreg",
    )
    assert result["zone"] == "aro", f"(0.5, 1.0) deberia clasificar como 'aro', salio {result['zone']!r}"
    assert 0.0 <= result["make_probability"] <= 1.0


def test_feb_without_player_uses_league_fg(shot_env):
    result_feb = shot_env.shot_make_probability(
        source="feb", x_m=0.5, y_m=1.0, shot_type="desconocido", model_name="logreg",
    )
    assert 0.0 <= result_feb["make_probability"] <= 1.0
    assert result_feb["player_prior_fg"] == 0.60, (
        f"sin player_id deberia usar el FG% de liga de la metadata (0.60), "
        f"salio {result_feb['player_prior_fg']}"
    )


def test_player_prior_specialist_scores_above_league_average(shot_env, tmp_path):
    # Base de datos SQLite de prueba real (esquema real via get_connection(),
    # no una fabricada a mano) con unos pocos tiros de un jugador
    # "especialista de aro" (siempre metido) -- el prior de ese jugador
    # deberia acercarse a su propio acierto (mucho mas alto que el 0.65
    # de liga), no quedarse en la media de liga.
    shot_conn = get_connection(tmp_path / "shots_test.db")
    shot_conn.executemany(
        "INSERT INTO shots (source, game_id, player_id, team_id, loc_x, loc_y, made, shot_type) "
        "VALUES ('nba', ?, '203999', 'ATL', 0, 5, 1, '2')",
        [(f"g{i}",) for i in range(1, 21)],  # 20 tiros de aro, TODOS metidos
    )
    shot_conn.commit()

    result_player = shot_env.shot_make_probability(
        source="nba", x_m=0.5, y_m=1.0, shot_type="2", model_name="logreg",
        conn=shot_conn, player_id="203999",
    )
    assert result_player["player_id"] == "203999"
    assert result_player["player_prior_fg"] > 0.65, (
        f"20/20 previos deberia encoger poco y quedar bastante por encima del "
        f"0.65 de liga, salio {result_player['player_prior_fg']}"
    )

    result_player_sin_historial = shot_env.shot_make_probability(
        source="nba", x_m=0.5, y_m=1.0, shot_type="2", model_name="logreg",
        conn=shot_conn, player_id="000000",  # jugador sin ningun tiro guardado
    )
    assert result_player_sin_historial["player_prior_fg"] == 0.65, (
        f"un jugador sin tiros previos deberia caer al FG% de liga (0.65), "
        f"salio {result_player_sin_historial['player_prior_fg']}"
    )
    shot_conn.close()


@pytest.mark.parametrize("label,args,kwargs", [
    ("fuente sin calibrar (acb)", ("acb", 1.0, 1.0, "2"), {}),
    ("shot_type invalido", ("nba", 1.0, 1.0, "libre"), {}),
    ("fuente calibrada pero no entrenada (eurocup)", ("eurocup", 1.0, 1.0, "2"), {"model_name": "logreg"}),
    ("feb con shot_type real en vez de 'desconocido'", ("feb", 1.0, 1.0, "2"), {"model_name": "logreg"}),
    ("player_id sin conn", ("nba", 1.0, 1.0, "2"), {"model_name": "logreg", "player_id": "203999"}),
])
def test_shot_error_cases_raise_prediction_error(shot_env, label, args, kwargs):
    with pytest.raises(shot_env.PredictionError):
        shot_env.shot_make_probability(*args, **kwargs)
