"""Features PRE-partido para predecir el resultado de un partido
(train.py, primer modelo -- ver decisiones_proyecto.md, Fase 6).

DIFERENCIA CLAVE con build_team_game_features() (src/features.py): esa
funcion calcula ritmo/rating/cuatro factores usando las estadisticas DEL
PROPIO PARTIDO -- validas para EDA/analisis retrospectivo (notebook,
Fase 4), pero INUTILIZABLES como features de un modelo que predice el
resultado de ESE MISMO partido: efg_pct, ortg, tov_pct... de un partido
estan practicamente determinados por si el equipo gano o perdio ESE
partido (eFG% alto casi siempre implica ganar). Usarlas tal cual seria
fuga de datos -- el modelo "prediria" con informacion que solo existe
despues de jugado el partido.

Este modulo transforma esas mismas metricas (ya validadas en el notebook
y en features.py) en features PRE-partido: para cada equipo y partido,
la media de sus partidos ANTERIORES en la misma temporada y fuente,
nunca del partido que se esta prediciendo. Solo asi el dataset resultante
sirve para prediccion real -- lo que se podria saber ANTES de que el
partido empiece.

Reutiliza build_team_game_features() en vez de recalcular nada desde
team_stats -- una sola fuente de verdad para las formulas de ritmo/
cuatro factores (notebook, features.py, features_pregame.py comparten
exactamente las mismas).
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from src.features import build_team_game_features

# Metricas de las que se calcula una media PRE-partido (todas ya
# validadas en features.py/notebook). "win" incluido a proposito: la
# media pre-partido de "win" es el % de victorias de la temporada hasta
# ese momento -- probablemente la feature individual mas informativa.
ROLLING_STAT_COLS: list[str] = [
    "pace_40", "ortg", "drtg", "efg_pct", "tov_pct", "orb_pct", "ft_rate", "win",
]


def _add_pregame_rolling(
    team_games: pd.DataFrame, stat_cols: list[str] = ROLLING_STAT_COLS
) -> pd.DataFrame:
    """Añade `<col>_pre` = media de `col` en los partidos ANTERIORES del
    mismo equipo, en la misma temporada y fuente -- shift(1) ANTES de la
    media expanding, para excluir siempre el partido que se esta
    calculando (si no se hiciera este shift, el propio partido entraria
    en su propia media y volveriamos a tener fuga de datos).

    También añade:
    - n_prior_games: cuantos partidos previos tiene ya el equipo esta
      temporada (0 en el primero) -- sirve para descartar arranques de
      temporada con muy poco historial (media de 1 partido = ruido, no
      señal), no para el modelo en si.
    - rest_days: dias desde el partido anterior del equipo (NaN en el
      primer partido de la temporada).
    """
    df = team_games.copy()
    # format="mixed": las distintas fuentes usan formatos de fecha
    # distintos en la misma columna (NBA "2026-04-10" vs Euroliga/EuroCup
    # "2024-10-03T19:00:00Z") -- sin "mixed", pandas infiere UN formato a
    # partir de las primeras filas y convierte en silencio a NaT las que
    # no encajan (bug real encontrado validando este modulo: se perdian
    # TODOS los partidos de NBA por NaT en rest_days). errors="coerce" +
    # la comprobacion de abajo para seguir avisando ante una fecha
    # realmente rara, en vez de asumir que "mixed" lo resuelve todo.
    df["date_parsed"] = pd.to_datetime(df["date"], utc=True, format="mixed", errors="coerce")
    bad_dates = df["date"].notna() & df["date_parsed"].isna()
    if bad_dates.any():
        examples = df.loc[bad_dates, ["source", "game_id", "date"]].head(5).to_dict("records")
        raise ValueError(
            f"No se pudo parsear la fecha de {bad_dates.sum()} partidos -- "
            f"formato inesperado, revisar antes de continuar (ejemplos: {examples})"
        )
    df = df.sort_values(["source", "season", "team_id", "date_parsed"]).reset_index(drop=True)
    g = df.groupby(["source", "season", "team_id"], sort=False)

    for col in stat_cols:
        df[f"{col}_pre"] = g[col].transform(lambda s: s.shift(1).expanding().mean())

    df["n_prior_games"] = g.cumcount()
    df["prev_date"] = g["date_parsed"].shift(1)
    df["rest_days"] = (df["date_parsed"] - df["prev_date"]).dt.total_seconds() / 86400.0
    return df


def build_pregame_game_dataset(
    conn: sqlite3.Connection,
    sources: list[str] | None = None,
    trainable_only: bool = True,
    min_prior_games: int = 5,
) -> pd.DataFrame:
    """Una fila = un partido (no un equipo-partido). Features PRE-partido
    de local y visitante (medias de temporada hasta ANTES de este
    partido), su diferencia local-visitante (`diff_*_pre`, la forma que
    suele darle mas señal a un modelo lineal que los dos valores
    sueltos), descanso de cada equipo, y el target `home_win`.

    Descarta partidos donde el local o el visitante tienen menos de
    `min_prior_games` partidos previos en la temporada -- por diseño de
    calidad de dato (media de 1-2 partidos es ruido), no por fuga:
    simplemente no hay suficiente informacion pre-partido todavia tan al
    principio de temporada.

    `trainable_only=True` (por defecto) filtra a TRAINABLE_SOURCES, igual
    que build_team_game_features() -- ver src/scraper/base.py.
    """
    team_games = build_team_game_features(conn, sources=sources, trainable_only=trainable_only)
    if team_games.empty:
        return pd.DataFrame()

    pre = _add_pregame_rolling(team_games)

    # build_team_game_features() ya "aplana" cada partido a team_id/
    # opp_id, perdiendo cual de los dos era el local -- se recupera
    # directamente de games (unica fuente de verdad para home/away).
    games_df = pd.read_sql("SELECT source, game_id, home_team_id FROM games", conn)
    pre = pre.merge(games_df, on=["source", "game_id"], how="left")
    pre["is_home"] = pre["team_id"] == pre["home_team_id"]

    pre_cols = [f"{c}_pre" for c in ROLLING_STAT_COLS] + ["n_prior_games", "rest_days"]

    home_rename = {c: f"home_{c}" for c in pre_cols}
    home_rename["team_id"] = "home_team_id"
    home_rename["win"] = "home_win"
    home = pre.loc[pre["is_home"], ["source", "game_id", "season", "date", "team_id", "win", *pre_cols]]
    home = home.rename(columns=home_rename)

    away_rename = {c: f"away_{c}" for c in pre_cols}
    away_rename["team_id"] = "away_team_id"
    away = pre.loc[~pre["is_home"], ["source", "game_id", "team_id", "win", *pre_cols]]
    away = away.drop(columns=["win"]).rename(columns=away_rename)

    games = home.merge(away, on=["source", "game_id"], how="inner")

    for c in ROLLING_STAT_COLS:
        games[f"diff_{c}_pre"] = games[f"home_{c}_pre"] - games[f"away_{c}_pre"]
    games["diff_rest_days"] = games["home_rest_days"] - games["away_rest_days"]

    games = games[
        (games["home_n_prior_games"] >= min_prior_games)
        & (games["away_n_prior_games"] >= min_prior_games)
    ].reset_index(drop=True)

    return games.sort_values(["source", "date"]).reset_index(drop=True)
