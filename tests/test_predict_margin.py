"""Mecanica del modelo de MARGEN DE VICTORIA (train_margin.py, conectado
al API el 2026-09-22) -- misma disciplina que test_predict_game_outcome.py:
datos sinteticos (reutiliza la fixture game_outcome_env, mismos 4
equipos/22 partidos fabricados), modelos de mentira propios (un Pipeline
StandardScaler+Ridge y un XGBRegressor bareo, mismas clases reales que
train_margin.py -- no una LogisticRegression bareada como el modelo de
mentira del resultado de partido) guardados en un MODELS_DIR temporal,
nunca contra los .joblib reales de models/ (eso lo cubre aparte
tests/test_api_integration.py, con pytest.skip si no existen todavia).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import joblib
import pytest
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

from tests.test_predict_game_outcome import SOURCE, game_outcome_env  # noqa: F401 (fixture)


def _fit_dummy_margin_models(feature_cols: list[str]):
    """Ridge (Pipeline, igual que train_margin.py) + XGBRegressor bareo
    entrenados con datos aleatorios -- solo para probar la mecanica de
    carga/predict() (forma de entrada/salida), sin ningun sentido
    basketbolistico (igual que _fit_dummy_model() del resultado de
    partido). Se entrena con un DataFrame (no un array numpy pelado) a
    proposito, igual que train_margin.py (X_train = train_df[FEATURE_
    COLS]) -- si no, Pipeline.get_feature_names_out() cae a nombres
    genericos "x0".."x9" en vez de los nombres reales de columna, y
    _shap_contributions() (que SI recibe un X con nombres reales desde
    predict_game()) no podria mapear esos nombres genericos de vuelta a
    ninguna feature de FEATURE_COLS."""
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(40, len(feature_cols))), columns=feature_cols)
    y = rng.normal(scale=12.0, size=40)  # escala parecida a un margen real en puntos
    ridge = Pipeline([("scaler", StandardScaler()), ("reg", Ridge(alpha=1.0))]).fit(X, y)
    xgb = XGBRegressor(n_estimators=10, max_depth=2, random_state=0).fit(X, y)
    return ridge, xgb


@pytest.fixture()
def margin_env(game_outcome_env):
    """Parte de game_outcome_env (misma base de datos sintetica) y
    ademas entrena/guarda los modelos de margen de mentira en el mismo
    MODELS_DIR temporal (_MARGIN_MODEL_FILES ya apunta ahi, ver el
    propio fixture game_outcome_env)."""
    conn, predict_mod = game_outcome_env
    ridge, xgb = _fit_dummy_margin_models(predict_mod.FEATURE_COLS)
    joblib.dump(ridge, predict_mod._MARGIN_MODEL_FILES["ridge"])
    joblib.dump(xgb, predict_mod._MARGIN_MODEL_FILES["xgboost"])
    predict_mod._margin_model_cache.clear()
    return conn, predict_mod


def test_available_margin_models_lists_trained_only(margin_env):
    conn, predict_mod = margin_env
    assert set(predict_mod.available_margin_models()) == {"ridge", "xgboost"}


@pytest.mark.parametrize("margin_model_name", ["ridge", "xgboost"])
def test_predict_game_includes_predicted_margin(margin_env, margin_model_name):
    conn, predict_mod = margin_env
    result = predict_mod.predict_game(
        conn, SOURCE, "T1", "T2", model_name="logreg", margin_model_name=margin_model_name,
    )
    assert isinstance(result["predicted_margin"], float)
    assert result["margin_model"] == margin_model_name
    assert result["margin_shap_explanation"] is not None


def test_predicted_margin_shap_explanation_is_additive_and_covers_all_features(margin_env):
    conn, predict_mod = margin_env
    result = predict_mod.predict_game(
        conn, SOURCE, "T1", "T2", model_name="logreg", margin_model_name="ridge",
    )
    exp = result["margin_shap_explanation"]
    assert {c["feature"] for c in exp["contributions"]} == set(predict_mod.FEATURE_COLS)
    total = exp["base_value"] + sum(c["shap_value"] for c in exp["contributions"])
    # tolerancia mayor que la del modelo de resultado (1e-3): aqui la
    # escala nativa es puntos de margen (potencialmente decenas), no
    # probabilidad [0, 1], y predicted_margin en la respuesta ya viene
    # redondeado a 1 decimal (round(x, 1) en predict_game()) -- la
    # aditividad SIN ningun redondeo (contra predict() crudo) esta
    # verificada aparte con diff < 1e-9, ver docstring del modulo.
    assert total == pytest.approx(result["predicted_margin"], abs=0.1)


def test_unknown_margin_model_name_raises(margin_env):
    conn, predict_mod = margin_env
    with pytest.raises(predict_mod.PredictionError):
        predict_mod.predict_game(conn, SOURCE, "T1", "T2", margin_model_name="no_existe")


def test_load_margin_metadata_raises_when_missing(margin_env):
    conn, predict_mod = margin_env
    with pytest.raises(predict_mod.PredictionError):
        predict_mod.load_margin_metadata()
