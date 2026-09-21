"""_shap_contributions()/_original_feature_name() en src/predict.py
(AÑADIDO 2026-09-21) -- mecanica con modelos sinteticos, sin depender
de los .joblib reales del proyecto (esos se cubren aparte en
tests/test_api_integration.py, unico sitio con un Pipeline real
StandardScaler+LogisticRegression para el modelo de resultado de
partido -- el modelo de mentira de test_predict_game_outcome.py es una
LogisticRegression bareada, no un Pipeline).

Reutiliza las fixtures game_outcome_env/shot_env de
test_predict_game_outcome.py/test_predict_shot.py (mismos Pipelines que
los modelos reales) importandolas directamente en vez de duplicar la
construccion de los modelos de mentira.
"""
from __future__ import annotations

import pytest

import src.predict as predict_mod
from tests.test_predict_game_outcome import SOURCE, game_outcome_env  # noqa: F401 (fixture)
from tests.test_predict_shot import shot_env  # noqa: F401 (fixture)


@pytest.mark.parametrize("transformed_name,categorical_cols,expected", [
    ("diff_win_pre", frozenset(), "diff_win_pre"),
    ("num__dist_m", frozenset({"zone", "shot_type", "source"}), "dist_m"),
    ("cat__zone_aro", frozenset({"zone", "shot_type", "source"}), "zone"),
    ("cat__shot_type_2", frozenset({"zone", "shot_type", "source"}), "shot_type"),
    ("cat__source_nba", frozenset({"zone", "shot_type", "source"}), "source"),
])
def test_original_feature_name_mapping(transformed_name, categorical_cols, expected):
    assert predict_mod._original_feature_name(transformed_name, categorical_cols) == expected


def test_original_feature_name_unmapped_category_raises():
    with pytest.raises(predict_mod.PredictionError):
        predict_mod._original_feature_name("cat__nombre_desconocido_x", frozenset({"zone"}))


def test_game_outcome_shap_explanation_is_additive_and_covers_all_features(game_outcome_env):
    conn, pm = game_outcome_env
    result = pm.predict_game(conn, SOURCE, "T1", "T2", model_name="logreg")
    exp = result["shap_explanation"]

    assert {c["feature"] for c in exp["contributions"]} == set(pm.FEATURE_COLS)
    total = exp["base_value"] + sum(c["shap_value"] for c in exp["contributions"])
    assert total == pytest.approx(result["home_win_probability"], abs=1e-3)
    # ordenado por |shap_value| descendente -- lo primero que ve quien
    # consulta el API es la feature que mas peso, no la primera al azar.
    magnitudes = [abs(c["shap_value"]) for c in exp["contributions"]]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_shot_shap_explanation_aggregates_onehot_dummies_into_original_features(shot_env):
    result = shot_env.shot_make_probability(
        source="nba", x_m=0.5, y_m=1.0, shot_type="2", model_name="logreg",
    )
    exp = result["shap_explanation"]

    # 7 features ORIGINALES (dist_m/angle_deg/player_prior_fg/
    # player_prior_n_log/zone/shot_type/source) -- nunca las columnas
    # dummy one-hot expandidas (p. ej. "zone_aro" suelto).
    assert {c["feature"] for c in exp["contributions"]} == set(shot_env.SHOT_FEATURE_COLS)
    total = exp["base_value"] + sum(c["shap_value"] for c in exp["contributions"])
    assert total == pytest.approx(result["make_probability"], abs=1e-3)
