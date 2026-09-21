"""_source_home_advantage_elo()/_replay_elo()/add_elo_ratings()/
current_elo_ratings() en src/features_pregame.py (AÑADIDO 2026-09-21,
ver el bloque "Rating ELO opponent-adjusted" en ese modulo para la
verificacion empirica completa: roc_auc 0.7406 -> 0.7481 en el split
cronologico de train.py).

Todo con datos SINTETICOS a mano (sin basketball.db real) -- el
objetivo es verificar la MECANICA (sin fuga de datos, el ganador sube y
el perdedor baja, la regresion de temporada empuja hacia ELO_BASE, el
encogido de ventaja de local funciona), no reproducir cifras reales
(esas se verifican con basketball.db real, fuera de la suite de tests
rapidos -- ver el propio docstring del modulo)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features_pregame import (
    ELO_BASE,
    _one_row_per_game,
    _replay_elo,
    _source_home_advantage_elo,
    add_elo_ratings,
    current_elo_ratings,
)


def _games_ordered(rows: list[dict]) -> pd.DataFrame:
    """rows: lista de {source, season, home_team_id, away_team_id,
    home_score, away_score}, se les añade date_parsed/game_id
    secuenciales -- exactamente la forma que espera _replay_elo()."""
    df = pd.DataFrame(rows)
    df["game_id"] = [f"g{i}" for i in range(len(df))]
    df["date_parsed"] = pd.to_datetime("2025-01-01") + pd.to_timedelta(range(len(df)), unit="D")
    return df


def test_source_home_advantage_elo_shrinks_small_sample_toward_fallback():
    # "grande": 1000 partidos, 70% victoria local -- deberia quedar
    # cerca de su propia tasa real, lejos del fallback global (0.580).
    # "chica": 5 partidos, 100% victoria local -- con tan poca muestra
    # deberia quedar mucho mas cerca del fallback que de un 100% literal.
    games = pd.DataFrame({
        "source": ["grande"] * 1000 + ["chica"] * 5,
        "home_win": [1] * 700 + [0] * 300 + [1] * 5,
    })
    adv = _source_home_advantage_elo(games, k_shrink=300.0, fallback_rate=0.580)

    fallback_elo = 400.0 * np.log10(0.580 / 0.420)
    full_grande_elo = 400.0 * np.log10(0.70 / 0.30)
    full_chica_elo = 400.0 * np.log10(0.999 / 0.001)  # 100% real, referencia solo de magnitud

    assert abs(adv["grande"] - full_grande_elo) < abs(adv["grande"] - fallback_elo)
    assert abs(adv["chica"] - fallback_elo) < abs(adv["chica"] - full_chica_elo)


def test_replay_elo_pregame_value_excludes_current_and_reflects_past():
    # A juega dos veces de local contra B, ganando siempre por margen
    # comodo -- el pre-elo de A en el SEGUNDO partido tiene que ser
    # mayor que en el primero (recoge la victoria anterior), y el
    # pre-elo del primer partido tiene que ser exactamente ELO_BASE
    # (ningun partido anterior todavia).
    games = _games_ordered([
        {"source": "s", "season": "2025", "home_team_id": "A", "away_team_id": "B",
         "home_score": 90, "away_score": 70},
        {"source": "s", "season": "2025", "home_team_id": "A", "away_team_id": "B",
         "home_score": 88, "away_score": 75},
    ])
    home_pre, away_pre, final = _replay_elo(games)

    assert home_pre[0] == pytest.approx(ELO_BASE)
    assert away_pre[0] == pytest.approx(ELO_BASE)
    assert home_pre[1] > ELO_BASE  # A gano el primero -> sube antes del segundo
    assert away_pre[1] < ELO_BASE  # B perdio el primero -> baja antes del segundo
    assert final[("s", "A")] > home_pre[1]  # tras el 2o partido (tambien ganado) sigue subiendo
    assert final[("s", "B")] < away_pre[1]


def test_replay_elo_winner_gains_loser_loses_across_several_games():
    rng = np.random.default_rng(0)
    rows = []
    # A gana siempre (de local y de visitante, alternando) contra C, un
    # rival que siempre pierde -- deberia terminar claramente por
    # encima de ELO_BASE, y C claramente por debajo.
    for i in range(20):
        if i % 2 == 0:
            rows.append({"source": "s", "season": "2025", "home_team_id": "A", "away_team_id": "C",
                         "home_score": 90, "away_score": 70})
        else:
            rows.append({"source": "s", "season": "2025", "home_team_id": "C", "away_team_id": "A",
                         "home_score": 70, "away_score": 90})
    games = _games_ordered(rows)
    _h, _a, final = _replay_elo(games)

    assert final[("s", "A")] > ELO_BASE + 100
    assert final[("s", "C")] < ELO_BASE - 100


def test_replay_elo_season_transition_regresses_toward_base():
    # A gana un monton de partidos en la temporada 2024 (termina bien
    # por encima de ELO_BASE), luego cambia a la temporada 2025 -- el
    # pre-elo de su primer partido de 2025 tiene que estar MAS CERCA de
    # ELO_BASE que su rating al terminar 2024 (regresion a la media),
    # pero seguir por encima de ELO_BASE (regress_to_mean=0.5, no 1.0).
    rows = [
        {"source": "s", "season": "2024", "home_team_id": "A", "away_team_id": "B",
         "home_score": 90, "away_score": 70}
        for _ in range(10)
    ]
    rows.append({"source": "s", "season": "2025", "home_team_id": "A", "away_team_id": "B",
                  "home_score": 80, "away_score": 78})
    games = _games_ordered(rows)
    home_pre, _a, final = _replay_elo(games, regress_to_mean=0.5)

    rating_end_2024 = final[("s", "A")]  # provisional, se sobreescribe abajo; ver siguiente linea
    # el pre-elo del ULTIMO partido (el de 2025) ya lleva la regresion aplicada
    pre_2025 = home_pre[-1]
    assert ELO_BASE < pre_2025


def test_replay_elo_without_season_change_does_not_regress():
    rows = [
        {"source": "s", "season": "2025", "home_team_id": "A", "away_team_id": "B",
         "home_score": 90, "away_score": 70}
        for _ in range(5)
    ]
    games = _games_ordered(rows)
    home_pre, _a, _final = _replay_elo(games, regress_to_mean=0.5)
    # sin cambio de temporada, el pre-elo de A deberia subir (o quedar
    # igual) partido a partido, nunca bajar de golpe por una regresion
    # que no toca.
    assert all(b >= a - 1e-9 for a, b in zip(home_pre, home_pre[1:]))


def test_one_row_per_game_collapses_both_sides_to_single_row():
    team_games = pd.DataFrame({
        "source": ["s", "s"],
        "game_id": ["g0", "g0"],
        "season": ["2025", "2025"],
        "date_parsed": pd.to_datetime(["2025-01-01", "2025-01-01"]),
        "team_id": ["A", "B"],
        "opp_id": ["B", "A"],
        "points_for": [90, 70],
        "points_against": [70, 90],
        "is_home": [True, False],
    })
    one_game = _one_row_per_game(team_games)
    assert len(one_game) == 1
    row = one_game.iloc[0]
    assert row["home_team_id"] == "A" and row["away_team_id"] == "B"
    assert row["home_score"] == 90 and row["away_score"] == 70


def _synthetic_team_games(n_games: int = 6) -> pd.DataFrame:
    """team_games con la forma real (2 filas por partido, is_home ya
    puesto) para add_elo_ratings()/current_elo_ratings() -- A gana
    SIEMPRE (90 a 80), alternando de local/visitante, para poder
    comprobar que el rating final de A queda claramente por encima de
    ELO_BASE sin que el propio alternar local/visitante enmascare el
    resultado real de quien gana."""
    rows = []
    for i in range(n_games):
        home, away = ("A", "B") if i % 2 == 0 else ("B", "A")
        home_score, away_score = (90, 80) if home == "A" else (80, 90)
        date = pd.Timestamp("2025-01-01") + pd.Timedelta(days=i)
        rows.append({"source": "s", "game_id": f"g{i}", "season": "2025", "date_parsed": date,
                      "team_id": home, "opp_id": away, "points_for": home_score,
                      "points_against": away_score, "is_home": True})
        rows.append({"source": "s", "game_id": f"g{i}", "season": "2025", "date_parsed": date,
                      "team_id": away, "opp_id": home, "points_for": away_score,
                      "points_against": home_score, "is_home": False})
    return pd.DataFrame(rows)


def test_add_elo_ratings_adds_elo_pre_to_both_sides():
    team_games = _synthetic_team_games()
    out = add_elo_ratings(team_games)

    assert "elo_pre" in out.columns
    assert out["elo_pre"].notna().all()
    # el primer partido de la serie (g0) es el primero para ambos
    # equipos -- su elo_pre tiene que ser exactamente ELO_BASE.
    first_game = out[out["game_id"] == "g0"]
    assert (first_game["elo_pre"].to_numpy() == ELO_BASE).all()


def test_current_elo_ratings_returns_final_post_game_values():
    team_games = _synthetic_team_games()
    final = current_elo_ratings(team_games)

    assert set(k[1] for k in final) == {"A", "B"}
    # A gana siempre (90 a 80) tanto de local como de visitante ->
    # deberia terminar por encima de ELO_BASE, B por debajo.
    assert final[("s", "A")] > ELO_BASE
    assert final[("s", "B")] < ELO_BASE
    # el rating ACTUAL (tras el ultimo partido) tiene que ser distinto
    # (mas alto para A) que su pre-elo de ese mismo ultimo partido --
    # si no, current_elo_ratings() no estaria aplicando la actualizacion
    # del ultimo resultado.
    out = add_elo_ratings(team_games)
    last_game_id = team_games["game_id"].iloc[-1]
    last_row_a = out[(out["game_id"] == last_game_id) & (out["team_id"] == "A")].iloc[0]
    assert final[("s", "A")] != pytest.approx(last_row_a["elo_pre"])
