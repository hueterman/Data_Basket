"""Mecanica del modelo de resultado de partido (api/app.py + src/predict.py).

Migrado desde scripts/test_api.py (parte 1, "Mecanica") a pytest -- usa
datos sinteticos minimos (varios equipos, partidos fabricados a mano en
una base de datos SQLite de prueba, NO datos reales de ninguna fuente)
para comprobar que latest_team_features()/predict_game() calculan lo
que deberian y que los casos de error (equipo sin historial, source
vacio, modelo inexistente...) devuelven PredictionError en vez de
reventar, sin depender de si train.py se ha ejecutado ya o no. Un
pequeño modelo de mentira (LogisticRegression entrenado con datos
aleatorios, solo para poder llamar a predict_proba con la forma
correcta) se guarda en un MODELS_DIR temporal para no tocar los modelos
reales.

No hace falta red ni modelos reales entrenados -- se puede ejecutar en
CI sin mas que las dependencias de requirements.txt.
"""
from __future__ import annotations

import numpy as np
import joblib
import pytest
from sklearn.linear_model import LogisticRegression

from src.pipeline.db import get_connection
from src.pipeline.repository import upsert_boxscore, upsert_games, upsert_teams
from src.scraper.base import BoxscoreData, GameRef, PlayerStatLine, Team, TeamStatLine

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


def _build_fixture_db(db_path) -> None:
    """Fabrica una base de datos minima con 4 equipos y 22 partidos
    jugados. T1 (6 partidos, todos ganados) y T2 (solo 2 partidos, sin
    temporada anterior -- a proposito, para probar el aviso de "poco
    historial") vienen del script original; T4 (12 partidos en
    2024-25 + 2 en 2025-26, mismo numero de partidos actuales que T2
    pero CON arrastre de temporada anterior) prueba
    used_season_carryover."""
    conn = get_connection(db_path)
    upsert_teams(conn, [
        Team(team_id="T1", name="Equipo Uno", source=SOURCE),
        Team(team_id="T2", name="Equipo Dos", source=SOURCE),
        Team(team_id="T3", name="Rival Generico", source=SOURCE),
        Team(team_id="T4", name="Equipo Con Historial Previo", source=SOURCE),
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

    # T4: SOLO 2 partidos en la temporada actual, igual que T2 -- pero con
    # una temporada ANTERIOR real (2024-25, 12 partidos, >= MIN_PRIOR_
    # SEASON_GAMES) detras. A diferencia de T2, latest_team_features(T4)
    # deberia salir con used_season_carryover=True y predict_game() NO
    # deberia avisar de "poco historial" para T4 pese a tener los mismos
    # 2 partidos esta temporada que T2.
    for i in range(12):
        gid = f"g_t4_prev_{i}"
        games.append(GameRef(
            game_id=gid, source=SOURCE, season="2024-25", round=i, date=f"2024-10-{i+1:02d}",
            home_team_id="T4", away_team_id="T3", home_score=78, away_score=65, played=True,
        ))
        boxscores.append(BoxscoreData(game_id=gid, team_stats=[
            _team_stat_line(gid, "T4", made2=19, att2=36, made3=5, att3=14, ftm=8, fta=10, orb=9, drb=24, tov=11),
            _team_stat_line(gid, "T3", made2=16, att2=39, made3=4, att3=15, ftm=8, fta=11, orb=11, drb=21, tov=13),
        ], player_stats=_player_stats(gid, 'T4') + _player_stats(gid, 'T3')))
    for i in range(2):
        gid = f"g_t4_cur_{i}"
        games.append(GameRef(
            game_id=gid, source=SOURCE, season="2025-26", round=i, date=f"2025-10-{i+1:02d}",
            home_team_id="T4", away_team_id="T3", home_score=75, away_score=68, played=True,
        ))
        boxscores.append(BoxscoreData(game_id=gid, team_stats=[
            _team_stat_line(gid, "T4", made2=18, att2=37, made3=4, att3=14, ftm=7, fta=9, orb=8, drb=23, tov=12),
            _team_stat_line(gid, "T3", made2=16, att2=38, made3=4, att3=15, ftm=9, fta=12, orb=10, drb=20, tov=13),
        ], player_stats=_player_stats(gid, 'T4') + _player_stats(gid, 'T3')))

    upsert_games(conn, games)
    for bs in boxscores:
        upsert_boxscore(conn, SOURCE, bs)
    conn.close()


def _fit_dummy_model(feature_cols: list[str]):
    """Modelo de mentira, SOLO para probar la mecanica de carga/predict_proba
    (forma de entrada/salida) -- nunca se compara su probabilidad con
    nada real, no tiene ningun sentido basketbolistico."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, len(feature_cols)))
    y = (X[:, 0] > 0).astype(int)
    model = LogisticRegression().fit(X, y)
    return model


@pytest.fixture()
def game_outcome_env(tmp_path):
    """Base de datos sintetica + modelo de mentira en un MODELS_DIR
    temporal (nunca toca los modelos/datos reales del proyecto)."""
    db_path = tmp_path / "fixture.db"
    _build_fixture_db(db_path)

    import src.predict as predict_mod
    predict_mod.MODELS_DIR = tmp_path
    predict_mod._MODEL_FILES = {
        "logreg": tmp_path / "game_outcome_logreg.joblib",
        "xgboost": tmp_path / "game_outcome_xgb.joblib",
    }
    predict_mod._model_cache.clear()
    model = _fit_dummy_model(predict_mod.FEATURE_COLS)
    joblib.dump(model, predict_mod._MODEL_FILES["logreg"])
    joblib.dump(model, predict_mod._MODEL_FILES["xgboost"])

    conn = get_connection(db_path)
    yield conn, predict_mod
    conn.close()


def test_team_with_full_history_has_perfect_win_rate(game_outcome_env):
    conn, predict_mod = game_outcome_env
    feats_t1 = predict_mod.latest_team_features(conn, SOURCE, "T1")
    assert feats_t1["n_games"] == 6, "T1 deberia tener 6 partidos jugados"
    assert feats_t1["win"] == 1.0, "T1 gano los 6 -- media de 'win' deberia ser 1.0"


def test_low_history_team_triggers_warning(game_outcome_env):
    conn, predict_mod = game_outcome_env
    result = predict_mod.predict_game(conn, SOURCE, "T1", "T2", model_name="logreg")
    assert result["home_games_played"] == 6 and result["away_games_played"] == 2
    assert any("Visitante" in w for w in result["warnings"]), (
        "T2 tiene solo 2 partidos (< 5) y SIN temporada anterior -- deberia "
        "salir un aviso de poco historial"
    )
    assert result["away_used_season_carryover"] is False


def test_season_carryover_suppresses_low_history_warning(game_outcome_env):
    conn, predict_mod = game_outcome_env
    # T4 tiene los mismos 2 partidos esta temporada que T2, pero SI tiene
    # una temporada anterior real (12 partidos, 2024-25) -- no deberia
    # avisar de "poco historial" pese a n_games==2, y latest_team_features()
    # debe marcar used_season_carryover=True.
    feats_t4 = predict_mod.latest_team_features(conn, SOURCE, "T4")
    assert feats_t4["n_games"] == 2
    assert feats_t4["used_season_carryover"] is True
    # win_pre encogido: ni el 100% puro de los 2 partidos de esta temporada
    # ni el 100% puro de la temporada anterior por separado -- se comprueba
    # que sigue en el rango valido [0, 1] (en este caso concreto, todo
    # victorias en ambos lados, sigue dando 1.0).
    assert 0.0 < feats_t4["win"] <= 1.0

    result_t4 = predict_mod.predict_game(conn, SOURCE, "T1", "T4", model_name="logreg")
    assert not any("Visitante" in w for w in result_t4["warnings"]), (
        "T4 tiene arrastre de temporada anterior -- NO deberia salir el aviso de poco historial"
    )
    assert result_t4["away_used_season_carryover"] is True


@pytest.mark.parametrize("label,call", [
    ("mismo equipo local/visitante",
     lambda predict_mod, conn: predict_mod.predict_game(conn, SOURCE, "T1", "T1")),
    ("equipo inexistente",
     lambda predict_mod, conn: predict_mod.latest_team_features(conn, SOURCE, "NO_EXISTE")),
    ("source sin datos",
     lambda predict_mod, conn: predict_mod.latest_team_features(conn, "nba", "T1")),
    ("modelo desconocido",
     lambda predict_mod, conn: predict_mod.predict_game(conn, SOURCE, "T1", "T2", model_name="no_existe")),
])
def test_error_cases_raise_prediction_error(game_outcome_env, label, call):
    conn, predict_mod = game_outcome_env
    with pytest.raises(predict_mod.PredictionError):
        call(predict_mod, conn)
