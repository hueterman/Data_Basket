"""Integracion real del API REST (api/app.py) contra los modelos/datos
reales del proyecto, SOLO si ya existen.

Migrado desde scripts/test_api.py (parte 2, "Integracion real") a
pytest. A diferencia de los tests de mecanica (test_predict_game_
outcome.py, test_predict_shot.py), esto pega contra
models/game_outcome_*.joblib y data/processed/basketball.db reales --
ambos estan en .gitignore y no existen en un checkout limpio de CI, asi
que cada test se salta (pytest.skip) en vez de fallar cuando faltan,
igual que el script original avisaba e interrumpia sin marcar fallo.
"""
from __future__ import annotations

import sys

import pytest

from src.pipeline.db import get_connection


@pytest.fixture()
def real_predict_mod():
    """Restaura MODELS_DIR al real (los tests de mecanica lo redirigen a
    un directorio temporal) y salta si los modelos reales no existen."""
    import src.predict as predict_mod
    real_models_dir = predict_mod.PROJECT_ROOT / "models"
    predict_mod.MODELS_DIR = real_models_dir
    predict_mod._MODEL_FILES = {
        "logreg": real_models_dir / "game_outcome_logreg.joblib",
        "xgboost": real_models_dir / "game_outcome_xgb.joblib",
    }
    predict_mod._model_cache.clear()

    if not predict_mod.available_models():
        pytest.skip(
            "sin modelos reales en models/ todavia -- ejecuta 'python train.py' "
            "primero para probar esta parte"
        )
    return predict_mod


@pytest.fixture()
def api_client(real_predict_mod):
    api_dir = real_predict_mod.PROJECT_ROOT / "api"
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))
    import app as api_app  # api/app.py

    return api_app.app.test_client()


def test_health_and_docs_endpoints(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200

    # Documentacion OpenAPI/Swagger -- comprueba que el spec es JSON
    # valido con los 6 paths reales y que /docs/ (Swagger UI) responde
    # 200, no que el contenido sea perfecto (eso ya lo valida
    # openapi-spec-validator aparte, ver api/README.md).
    r = api_client.get("/openapi.json")
    spec = r.get_json()
    assert r.status_code == 200
    expected_paths = {"/health", "/model/info", "/model/info/shot", "/teams", "/predict", "/predict_shot"}
    assert expected_paths <= set(spec.get("paths", {})), (
        f"faltan paths en openapi.json: {expected_paths - set(spec.get('paths', {}))}"
    )

    r = api_client.get("/docs/")
    assert r.status_code == 200

    r = api_client.get("/model/info")
    assert r.status_code == 200


def test_predict_with_real_teams(api_client):
    conn = get_connection()
    row = conn.execute(
        "SELECT source, COUNT(*) AS n FROM games WHERE played = 1 GROUP BY source ORDER BY n DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        pytest.skip("sin partidos jugados en la base de datos real -- no se puede probar /predict")
    real_source = row["source"]

    r = api_client.get(f"/teams?source={real_source}")
    teams_real = r.get_json()
    assert r.status_code == 200 and len(teams_real) >= 2

    home_id, away_id = teams_real[0]["team_id"], teams_real[1]["team_id"]
    r = api_client.post("/predict", json={
        "source": real_source, "home_team_id": home_id, "away_team_id": away_id,
    })
    pred = r.get_json()
    assert r.status_code == 200
    assert 0.0 <= pred["home_win_probability"] <= 1.0

    # Peticion mal formada -- debe devolver 400, no un 500.
    r = api_client.post("/predict", json={"source": real_source, "home_team_id": home_id})
    assert r.status_code == 400


def test_predict_shot_with_real_models(api_client, real_predict_mod):
    if not real_predict_mod.available_shot_models():
        pytest.skip(
            "sin modelo de tiro real en models/ todavia -- ejecuta 'python "
            "train_shot.py' para probar /predict_shot"
        )

    r = api_client.get("/model/info/shot")
    assert r.status_code == 200

    r_rim = api_client.post("/predict_shot", json={
        "source": "nba", "x_m": 0.3, "y_m": 0.5, "shot_type": "2",
    })
    r_three = api_client.post("/predict_shot", json={
        "source": "nba", "x_m": 0.5, "y_m": 7.5, "shot_type": "3",
    })
    assert r_rim.status_code == 200 and r_three.status_code == 200
    p_rim = r_rim.get_json()["make_probability"]
    p_three = r_three.get_json()["make_probability"]
    assert p_rim > p_three, (
        f"un tiro junto al aro deberia ser mas facil que un triple de arco "
        f"(salio {p_rim} vs {p_three}) -- comprobacion de cordura basica"
    )

    r_feb_ok = api_client.post("/predict_shot", json={
        "source": "feb", "x_m": 0.5, "y_m": 1.0, "shot_type": "desconocido",
    })
    assert r_feb_ok.status_code == 200

    r_feb_bad_type = api_client.post("/predict_shot", json={
        "source": "feb", "x_m": 1.0, "y_m": 1.0, "shot_type": "2",
    })
    assert r_feb_bad_type.status_code == 400

    r_uncalibrated = api_client.post("/predict_shot", json={
        "source": "acb", "x_m": 1.0, "y_m": 1.0, "shot_type": "2",
    })
    assert r_uncalibrated.status_code == 400

    # player_id real (Mikal Bridges, "1628969") -- el prior deberia ser
    # SU acierto real en "aro" (via _player_zone_prior(), calculado en
    # vivo contra la base de datos real), no el de liga generico.
    r_rim_player = api_client.post("/predict_shot", json={
        "source": "nba", "x_m": 0.3, "y_m": 0.5, "shot_type": "2",
        "player_id": "1628969",
    })
    assert r_rim_player.status_code == 200
    assert r_rim_player.get_json()["player_id"] == "1628969"
