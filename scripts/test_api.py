r"""Prueba de humo del API REST (api/app.py + src/predict.py).

Dos partes:
1. MECANICA, con datos sinteticos minimos (2 equipos, un puñado de
   partidos fabricados a mano en una base de datos SQLite de prueba,
   NO datos reales de ninguna fuente) -- comprueba que
   latest_team_features()/predict_game() calculan lo que deberian y que
   los casos de error (equipo sin historial, source vacio, modelo
   inexistente...) devuelven PredictionError en vez de reventar, sin
   depender de si train.py se ha ejecutado ya o no. Un pequeño modelo
   de mentira (LogisticRegression entrenado con datos aleatorios, solo
   para poder llamar a predict_proba con la forma correcta) se guarda
   en un MODELS_DIR temporal para no tocar los modelos reales.
2. INTEGRACION real, SOLO si ya existen los modelos reales entrenados
   por train.py (models/game_outcome_*.joblib) y hay datos reales en
   data/processed/basketball.db -- monta un cliente de pruebas de Flask
   (sin levantar el servidor de verdad) y pega a /health, /model/info,
   /teams y /predict con dos equipos reales de la fuente con mas
   partidos, imprimiendo la respuesta real para poder revisarla a ojo
   antes de dar el API por buena. Se salta con un aviso si los modelos
   reales no existen todavia (no es un fallo de esta prueba).

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_api.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.pipeline.db import get_connection
from src.pipeline.repository import upsert_games, upsert_teams, upsert_boxscore
from src.scraper.base import BoxscoreData, GameRef, Player, PlayerStatLine, Team, TeamStatLine

SOURCE = "acb"  # fuente entrenable real, basta para fabricar datos sinteticos


def _team_stat_line(game_id: str, team_id: str, *, made2: int, att2: int, made3: int,
                     att3: int, ftm: int, fta: int, orb: int, drb: int, tov: int) -> TeamStatLine:
    return TeamStatLine(
        game_id=game_id, team_id=team_id,
        t2_made=made2, t2_att=att2, t3_made=made3, t3_att=att3,
        ft_made=ftm, ft_att=fta, reb_off=orb, reb_def=drb,
        ast=10, stl=5, turnovers=tov, blocks=2, fouls=15,
        val=made2 * 2 + made3 * 3 + ftm,
    )


def _player_stats(game_id: str, team_id: str) -> list[PlayerStatLine]:
    """5 jugadores/as con 40 minutos cada uno -- team_minutes = 200/5 = 40
    (partido completo sin prorroga), necesario para que pace_40/ortg/drtg
    en build_team_game_features() no salgan NaN (dependen de team_minutes,
    que sale de player_stats -- sin esto TODAS las features de ritmo
    quedan en NaN, bug de fixture ya pillado una vez al validar esto)."""
    return [
        PlayerStatLine(
            game_id=game_id, player_id=f"{team_id}_p{i}", player_name=f"Jugador {i}",
            team_id=team_id, minutes=40.0, points=8, rebounds=4, assists=2, val=10,
        )
        for i in range(5)
    ]


def build_fixture_db(db_path: Path) -> None:
    """Fabrica una base de datos minima con 2 equipos y 6 partidos
    jugados (3 cada uno como local, historial de sobra para pasar
    min_prior_games=5 solo si se combinan -- a proposito se deja a un
    equipo con menos de 5 para probar el aviso de "poco historial")."""
    conn = get_connection(db_path)
    upsert_teams(conn, [
        Team(team_id="T1", name="Equipo Uno", source=SOURCE),
        Team(team_id="T2", name="Equipo Dos", source=SOURCE),
        Team(team_id="T3", name="Rival Generico", source=SOURCE),
    ])

    games = []
    boxscores = []
    # T1 gana 6 partidos consecutivos contra T3 (equipo fuerte) -- fechas
    # en orden para que "temporada mas reciente" sea inequivoca.
    for i in range(6):
        gid = f"g_t1_{i}"
        games.append(GameRef(
            game_id=gid, source=SOURCE, season="2025-26", round=i, date=f"2025-10-{i+1:02d}",
            home_team_id="T1", away_team_id="T3", home_score=80, away_score=60, played=True,
        ))
        boxscores.append(BoxscoreData(game_id=gid, team_stats=[
            _team_stat_line(gid, "T1", made2=20, att2=35, made3=6, att3=15, ftm=8, fta=10, orb=10, drb=25, tov=10),
            _team_stat_line(gid, "T3", made2=15, att2=40, made3=4, att3=16, ftm=8, fta=12, orb=12, drb=20, tov=15),
        ], player_stats=_player_stats(gid, 'T1') + _player_stats(gid, 'T3')))

    # T2 solo tiene 2 partidos jugados esta temporada -- a proposito,
    # para comprobar el aviso de "poco historial" en predict_game().
    for i in range(2):
        gid = f"g_t2_{i}"
        games.append(GameRef(
            game_id=gid, source=SOURCE, season="2025-26", round=i, date=f"2025-10-{i+1:02d}",
            home_team_id="T2", away_team_id="T3", home_score=65, away_score=70, played=True,
        ))
        boxscores.append(BoxscoreData(game_id=gid, team_stats=[
            _team_stat_line(gid, "T2", made2=14, att2=38, made3=3, att3=14, ftm=6, fta=9, orb=8, drb=18, tov=14),
            _team_stat_line(gid, "T3", made2=17, att2=39, made3=5, att3=15, ftm=9, fta=11, orb=11, drb=22, tov=12),
        ], player_stats=_player_stats(gid, 'T2') + _player_stats(gid, 'T3')))

    upsert_games(conn, games)
    for bs in boxscores:
        upsert_boxscore(conn, SOURCE, bs)
    conn.close()


def fit_dummy_model(feature_cols: list[str]):
    """Modelo de mentira, SOLO para probar la mecanica de carga/predict_proba
    (forma de entrada/salida) -- nunca se compara su probabilidad con
    nada real, no tiene ningun sentido basketbolistico."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, len(feature_cols)))
    y = (X[:, 0] > 0).astype(int)
    model = LogisticRegression().fit(X, y)
    return model


def test_mechanics() -> None:
    print("=== 1) Mecanica (datos sinteticos, sin tocar la base de datos real) ===")
    tmp_dir = Path(tempfile.mkdtemp(prefix="test_api_"))
    db_path = tmp_dir / "fixture.db"
    build_fixture_db(db_path)

    import src.predict as predict_mod
    predict_mod.MODELS_DIR = tmp_dir
    predict_mod._MODEL_FILES = {
        "logreg": tmp_dir / "game_outcome_logreg.joblib",
        "xgboost": tmp_dir / "game_outcome_xgb.joblib",
    }
    predict_mod._model_cache.clear()
    model = fit_dummy_model(predict_mod.FEATURE_COLS)
    joblib.dump(model, predict_mod._MODEL_FILES["logreg"])
    joblib.dump(model, predict_mod._MODEL_FILES["xgboost"])

    conn = get_connection(db_path)

    feats_t1 = predict_mod.latest_team_features(conn, SOURCE, "T1")
    print(f"  latest_team_features(T1): n_games={feats_t1['n_games']} season={feats_t1['season']} "
          f"win={feats_t1['win']:.3f} ortg={feats_t1['ortg']:.1f}")
    assert feats_t1["n_games"] == 6, "T1 deberia tener 6 partidos jugados"
    assert feats_t1["win"] == 1.0, "T1 gano los 6 -- media de 'win' deberia ser 1.0"

    result = predict_mod.predict_game(conn, SOURCE, "T1", "T2", model_name="logreg")
    print(f"  predict_game(T1 local vs T2 visitante): "
          f"home_win_probability={result['home_win_probability']} "
          f"warnings={result['warnings']}")
    assert result["home_games_played"] == 6 and result["away_games_played"] == 2
    assert any("Visitante" in w for w in result["warnings"]), (
        "T2 tiene solo 2 partidos (< 5) -- deberia salir un aviso de poco historial"
    )

    # Casos de error esperados -- deben lanzar PredictionError, no reventar.
    for label, fn in [
        ("mismo equipo local/visitante",
         lambda: predict_mod.predict_game(conn, SOURCE, "T1", "T1")),
        ("equipo inexistente",
         lambda: predict_mod.latest_team_features(conn, SOURCE, "NO_EXISTE")),
        ("source sin datos",
         lambda: predict_mod.latest_team_features(conn, "nba", "T1")),
        ("modelo desconocido",
         lambda: predict_mod.predict_game(conn, SOURCE, "T1", "T2", model_name="no_existe")),
    ]:
        try:
            fn()
            raise AssertionError(f"  ERROR: {label!r} deberia haber lanzado PredictionError")
        except predict_mod.PredictionError as exc:
            print(f"  OK, error esperado ({label}): {exc}")

    conn.close()
    print("  Mecanica: todo OK.\n")


def fit_dummy_shot_model():
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
        # player_prior_fg/player_prior_n_log añadidas 2026-09-21 -- ver
        # add_shot_priors() en src/features.py y _player_zone_prior() en
        # src/predict.py. Mismas columnas que SHOT_FEATURE_COLS real.
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


def test_shot_mechanics() -> None:
    print("=== 1b) Mecanica del modelo de TIRO (datos/modelo sinteticos) ===")
    import src.predict as predict_mod

    tmp_dir = Path(tempfile.mkdtemp(prefix="test_api_shot_"))
    predict_mod.MODELS_DIR = tmp_dir
    predict_mod._SHOT_MODEL_FILES = {
        "logreg": tmp_dir / "shot_make_logreg.joblib",
        "xgboost": tmp_dir / "shot_make_xgb.joblib",
    }
    predict_mod._shot_model_cache.clear()
    model = fit_dummy_shot_model()
    joblib.dump(model, predict_mod._SHOT_MODEL_FILES["logreg"])
    joblib.dump(model, predict_mod._SHOT_MODEL_FILES["xgboost"])
    # Metadata de mentira -- limita "sources" a nba/euroleague, para
    # poder probar el rechazo de una fuente calibrada pero NO entrenada
    # (eurocup SI esta calibrada en SHOT_COORD_CALIBRATION pero no
    # aparece aqui a proposito).
    import json
    with open(tmp_dir / "shot_make_metadata.json", "w", encoding="utf-8") as f:
        json.dump({
            "sources": ["nba", "euroleague", "feb"],
            "league_zone_fg": [
                {"source": "nba", "zone": "aro", "fg_pct": 0.65},
                {"source": "feb", "zone": "aro", "fg_pct": 0.60},
            ],
            "shot_prior_k_shrink": 20.0,
        }, f)

    result = predict_mod.shot_make_probability(
        source="nba", x_m=0.5, y_m=1.0, shot_type="2", model_name="logreg",
    )
    print(f"  shot_make_probability(nba, cerca del aro, tiro de 2): {result}")
    assert result["zone"] == "aro", f"(0.5, 1.0) deberia clasificar como 'aro', salio {result['zone']!r}"
    assert 0.0 <= result["make_probability"] <= 1.0

    result_feb = predict_mod.shot_make_probability(
        source="feb", x_m=0.5, y_m=1.0, shot_type="desconocido", model_name="logreg",
    )
    print(f"  shot_make_probability(feb, shot_type='desconocido'): {result_feb}")
    assert 0.0 <= result_feb["make_probability"] <= 1.0
    assert result_feb["player_prior_fg"] == 0.60, (
        f"sin player_id deberia usar el FG% de liga de la metadata (0.60), "
        f"salio {result_feb['player_prior_fg']}"
    )

    # player_id (AÑADIDO 2026-09-21): base de datos SQLite de prueba real
    # (esquema real via get_connection(), no una fabricada a mano) con
    # unos pocos tiros de un jugador "especialista de aro" (siempre
    # metido) -- el prior de ese jugador deberia acercarse a su propio
    # acierto (mucho mas alto que el 0.65 de liga), no quedarse en la
    # media de liga.
    shot_db_path = tmp_dir / "shots_test.db"
    shot_conn = get_connection(shot_db_path)
    shot_conn.executemany(
        "INSERT INTO shots (source, game_id, player_id, team_id, loc_x, loc_y, made, shot_type) "
        "VALUES ('nba', ?, '203999', 'ATL', 0, 5, 1, '2')",
        [(f"g{i}",) for i in range(1, 21)],  # 20 tiros de aro, TODOS metidos
    )
    shot_conn.commit()

    result_player = predict_mod.shot_make_probability(
        source="nba", x_m=0.5, y_m=1.0, shot_type="2", model_name="logreg",
        conn=shot_conn, player_id="203999",
    )
    print(f"  shot_make_probability(nba, player_id='203999', especialista de aro): {result_player}")
    assert result_player["player_id"] == "203999"
    assert result_player["player_prior_fg"] > 0.65, (
        f"20/20 previos deberia encoger poco y quedar bastante por encima del "
        f"0.65 de liga, salio {result_player['player_prior_fg']}"
    )

    result_player_sin_historial = predict_mod.shot_make_probability(
        source="nba", x_m=0.5, y_m=1.0, shot_type="2", model_name="logreg",
        conn=shot_conn, player_id="000000",  # jugador sin ningun tiro guardado
    )
    print(f"  shot_make_probability(nba, player_id sin historial): {result_player_sin_historial}")
    assert result_player_sin_historial["player_prior_fg"] == 0.65, (
        f"un jugador sin tiros previos deberia caer al FG% de liga (0.65), "
        f"salio {result_player_sin_historial['player_prior_fg']}"
    )
    shot_conn.close()

    for label, fn in [
        ("fuente sin calibrar (acb)",
         lambda: predict_mod.shot_make_probability("acb", 1.0, 1.0, "2")),
        ("shot_type invalido",
         lambda: predict_mod.shot_make_probability("nba", 1.0, 1.0, "libre")),
        ("fuente calibrada pero no entrenada (eurocup)",
         lambda: predict_mod.shot_make_probability("eurocup", 1.0, 1.0, "2", model_name="logreg")),
        ("feb con shot_type real en vez de 'desconocido'",
         lambda: predict_mod.shot_make_probability("feb", 1.0, 1.0, "2", model_name="logreg")),
        ("player_id sin conn",
         lambda: predict_mod.shot_make_probability("nba", 1.0, 1.0, "2", model_name="logreg", player_id="203999")),
    ]:
        try:
            fn()
            raise AssertionError(f"  ERROR: {label!r} deberia haber lanzado PredictionError")
        except predict_mod.PredictionError as exc:
            print(f"  OK, error esperado ({label}): {exc}")

    print("  Mecanica de tiro: todo OK.\n")


def test_real_api_if_available() -> None:
    print("=== 2) Integracion real (API + modelos reales de train.py, si existen) ===")
    import src.predict as predict_mod
    # Restaurar MODELS_DIR real (test_mechanics() lo habia redirigido a un
    # directorio temporal) antes de comprobar los modelos de verdad.
    real_models_dir = predict_mod.PROJECT_ROOT / "models"
    predict_mod.MODELS_DIR = real_models_dir
    predict_mod._MODEL_FILES = {
        "logreg": real_models_dir / "game_outcome_logreg.joblib",
        "xgboost": real_models_dir / "game_outcome_xgb.joblib",
    }
    predict_mod._model_cache.clear()

    if not predict_mod.available_models():
        print("  (sin modelos reales en models/ todavia -- ejecuta 'python train.py' "
              "primero para probar esta parte. No es un fallo.)\n")
        return

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
    import app as api_app  # api/app.py

    client = api_app.app.test_client()

    r = client.get("/health")
    print(f"  GET /health -> {r.status_code} {r.get_json()}")
    assert r.status_code == 200

    # Documentacion OpenAPI/Swagger (AÑADIDO 2026-09-21, punto 6 del
    # roadmap) -- comprueba que el spec es JSON valido con los 6 paths
    # reales y que /docs/ (Swagger UI) responde 200, no que el contenido
    # sea perfecto (eso ya lo valida openapi-spec-validator aparte, ver
    # api/README.md).
    r = client.get("/openapi.json")
    spec = r.get_json()
    print(f"  GET /openapi.json -> {r.status_code} paths={sorted((spec or {}).get('paths', {}))}")
    assert r.status_code == 200
    expected_paths = {"/health", "/model/info", "/model/info/shot", "/teams", "/predict", "/predict_shot"}
    assert expected_paths <= set(spec.get("paths", {})), (
        f"faltan paths en openapi.json: {expected_paths - set(spec.get('paths', {}))}"
    )

    r = client.get("/docs/")
    print(f"  GET /docs/ -> {r.status_code}")
    assert r.status_code == 200

    r = client.get("/model/info")
    info = r.get_json()
    print(f"  GET /model/info -> {r.status_code} available_models={info.get('available_models')} "
          f"xgboost.accuracy={info.get('metrics', {}).get('xgboost', {}).get('accuracy')}")
    assert r.status_code == 200

    conn = get_connection()
    row = conn.execute(
        "SELECT source, COUNT(*) AS n FROM games WHERE played = 1 GROUP BY source ORDER BY n DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        print("  (sin partidos jugados en la base de datos real -- no se puede probar /predict.)\n")
        return
    real_source = row["source"]

    r = client.get(f"/teams?source={real_source}")
    teams_real = r.get_json()
    print(f"  GET /teams?source={real_source} -> {r.status_code}, {len(teams_real)} equipos")
    assert r.status_code == 200 and len(teams_real) >= 2

    home_id, away_id = teams_real[0]["team_id"], teams_real[1]["team_id"]
    r = client.post("/predict", json={
        "source": real_source, "home_team_id": home_id, "away_team_id": away_id,
    })
    pred = r.get_json()
    print(f"  POST /predict ({real_source}: {home_id} local vs {away_id} visitante) -> "
          f"{r.status_code} {pred}")
    assert r.status_code == 200
    assert 0.0 <= pred["home_win_probability"] <= 1.0

    # Peticion mal formada -- debe devolver 400, no un 500.
    r = client.post("/predict", json={"source": real_source, "home_team_id": home_id})
    print(f"  POST /predict (sin away_team_id) -> {r.status_code} {r.get_json()}")
    assert r.status_code == 400

    if predict_mod.available_shot_models():
        r = client.get("/model/info/shot")
        shot_info = r.get_json()
        print(f"  GET /model/info/shot -> {r.status_code} available_models="
              f"{shot_info.get('available_models')} sources={shot_info.get('sources')} "
              f"xgboost.accuracy={shot_info.get('metrics', {}).get('xgboost', {}).get('accuracy')}")
        assert r.status_code == 200

        r_rim = client.post("/predict_shot", json={
            "source": "nba", "x_m": 0.3, "y_m": 0.5, "shot_type": "2",
        })
        r_three = client.post("/predict_shot", json={
            "source": "nba", "x_m": 0.5, "y_m": 7.5, "shot_type": "3",
        })
        print(f"  POST /predict_shot (nba, tiro junto al aro) -> {r_rim.status_code} {r_rim.get_json()}")
        print(f"  POST /predict_shot (nba, triple de arco) -> {r_three.status_code} {r_three.get_json()}")
        assert r_rim.status_code == 200 and r_three.status_code == 200
        p_rim = r_rim.get_json()["make_probability"]
        p_three = r_three.get_json()["make_probability"]
        assert p_rim > p_three, (
            f"un tiro junto al aro deberia ser mas facil que un triple de arco "
            f"(salio {p_rim} vs {p_three}) -- comprobacion de cordura basica"
        )

        r_feb_ok = client.post("/predict_shot", json={
            "source": "feb", "x_m": 0.5, "y_m": 1.0, "shot_type": "desconocido",
        })
        print(f"  POST /predict_shot (feb, junto al aro, shot_type='desconocido') -> "
              f"{r_feb_ok.status_code} {r_feb_ok.get_json()}")
        assert r_feb_ok.status_code == 200

        r_feb_bad_type = client.post("/predict_shot", json={
            "source": "feb", "x_m": 1.0, "y_m": 1.0, "shot_type": "2",
        })
        print(f"  POST /predict_shot (feb, shot_type='2' -- deberia rechazarse) -> "
              f"{r_feb_bad_type.status_code} {r_feb_bad_type.get_json()}")
        assert r_feb_bad_type.status_code == 400

        r_uncalibrated = client.post("/predict_shot", json={
            "source": "acb", "x_m": 1.0, "y_m": 1.0, "shot_type": "2",
        })
        print(f"  POST /predict_shot (acb, sin calibrar) -> "
              f"{r_uncalibrated.status_code} {r_uncalibrated.get_json()}")
        assert r_uncalibrated.status_code == 400

        # player_id real (AÑADIDO 2026-09-21): mismo tiro junto al aro que
        # r_rim de arriba, pero con un jugador real de la base de datos
        # (Mikal Bridges, "1628969", el de mas partidos en player_stats
        # para nba a fecha de esta sesion) -- el prior deberia ser SU
        # acierto real en "aro" (via _player_zone_prior(), calculado en
        # vivo contra la base de datos real), no el de liga generico.
        r_rim_player = client.post("/predict_shot", json={
            "source": "nba", "x_m": 0.3, "y_m": 0.5, "shot_type": "2",
            "player_id": "1628969",
        })
        print(f"  POST /predict_shot (nba, junto al aro, player_id='1628969') -> "
              f"{r_rim_player.status_code} {r_rim_player.get_json()}")
        assert r_rim_player.status_code == 200
        assert r_rim_player.get_json()["player_id"] == "1628969"
    else:
        print("  (sin modelo de tiro real en models/ todavia -- ejecuta 'python train_shot.py' "
              "para probar /predict_shot. No es un fallo.)")

    print("  Integracion real: todo OK.\n")


if __name__ == "__main__":
    test_mechanics()
    test_shot_mechanics()
    test_real_api_if_available()
    print("Todas las pruebas pasaron.")
