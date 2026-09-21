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

import numpy as np
import pandas as pd

from src.features import build_team_game_features

# Metricas de las que se calcula una media PRE-partido (todas ya
# validadas en features.py/notebook). "win" incluido a proposito: la
# media pre-partido de "win" es el % de victorias de la temporada hasta
# ese momento -- probablemente la feature individual mas informativa.
ROLLING_STAT_COLS: list[str] = [
    "pace_40", "ortg", "drtg", "efg_pct", "tov_pct", "orb_pct", "ft_rate", "win",
]

# Peso (en partidos "virtuales" de temporada actual) del encogido
# bayesiano de _add_season_carryover() -- mismo patron que SHOT_PRIOR_
# K_SHRINK en src/features.py (add_shot_priors()), aqui aplicado al
# arrastre de la temporada anterior de un equipo en vez del prior de un
# jugador. Con 0 partidos jugados esta temporada, la media "pre" queda
# igual a la de la temporada anterior; con SEASON_PRIOR_K_SHRINK
# partidos ya jugados esta temporada, pesan igual; mas alla, domina la
# temporada actual. 10 partidos es un valor razonado (mitad de camino
# entre "confiar del todo en la temporada pasada" y "ignorarla en
# cuanto hay un puñado de partidos nuevos"), no ajustado por busqueda
# exhaustiva -- ver docstring de _add_season_carryover() para la
# verificacion empirica de que el arrastre tiene señal real antes de
# usar este numero.
SEASON_PRIOR_K_SHRINK = 10.0

# Una temporada solo cuenta como fuente de arrastre si tuvo al menos
# esta cantidad de partidos -- evita arrastrar una temporada-muñón (p.
# ej. las fases finales de 1-3 partidos de Tercera FEB antes de
# 2026-27, ya documentadas como no comparables en volumen en
# ingest_historical.py) como si fuera una temporada completa real.
MIN_PRIOR_SEASON_GAMES = 10


def _add_season_carryover(
    df: pd.DataFrame, stat_cols: list[str] = ROLLING_STAT_COLS,
    k_shrink: float = SEASON_PRIOR_K_SHRINK,
    min_prior_season_games: int = MIN_PRIOR_SEASON_GAMES,
) -> pd.DataFrame:
    """Añade `prior_season_<col>` a `df` (una fila = equipo-partido, ya
    con `date_parsed`): la media de CADA columna en la temporada
    ANTERIOR real del mismo equipo -- NaN si no hay temporada anterior
    valida (primera temporada del equipo en esta fuente, o la anterior
    tuvo menos de `min_prior_season_games` partidos).

    MOTIVACION (verificado en datos reales antes de escribir esto,
    sesion 2026-09-21, no adivinado): para pares de temporadas
    consecutivas del mismo equipo con >=10 partidos cada una (461 pares
    reales en acb/eurocup/euroleague/nba, las unicas fuentes con
    suficiente continuidad de equipo entre temporadas -- las ligas FEB
    de nivel bajo tienen demasiado ascenso/descenso para formar pares),
    la correlacion entre el valor de una metrica en la temporada N y en
    la N-1 es alta en las 8 columnas: pace_40=0.944, tov_pct=0.842,
    orb_pct=0.772, ortg=0.630, ft_rate=0.630, win=0.612, drtg=0.553,
    efg_pct=0.521 -- lejos de cero, confirma que "como jugo el equipo la
    temporada pasada" es señal real sobre como va a jugar al principio
    de la siguiente, no ruido. Ademas, usar el win% de la temporada
    anterior como prediccion del win% de la actual da MAE=0.109, mejor
    que asumir 0.5 fijo (MAE=0.129, el equivalente de "sin ninguna
    informacion") -- justifica arrastrar esto en vez de dejar que el
    modelo arranque cada temporada a ciegas.

    COMO SE CALCULA (por TEMPORADA, no por partido -- el arrastre de un
    equipo a una temporada nueva es un unico numero fijo, conocido antes
    de que esa temporada empiece):
    1. Se ordenan las temporadas de cada (source, team_id) por su fecha
       REAL minima (`date_parsed.min()`), nunca por orden alfabetico del
       string `season` -- hallazgo real de esta verificacion: ACB tiene
       valores de `season` con un espacio inicial inconsistente
       (" 2014-15" junto a "2016-17" sin espacio) y NextGen usa codigos
       de sede que no son secuenciales como texto (p. ej. "JTF420" junto
       a "JTP21"/"JTB23") -- ordenar por fecha real evita ambos
       problemas sin necesitar un caso especial por fuente.
    2. Se calcula la media de cada columna sobre TODOS los partidos de
       esa temporada (no shift(1), la temporada entera -- se usa para
       predecir la SIGUIENTE temporada, no la propia).
    3. Se descarta como fuente de arrastre cualquier temporada con menos
       de `min_prior_season_games` partidos (ver MIN_PRIOR_SEASON_GAMES)
       -- evita arrastrar una fase final/promocion pequeña como si fuera
       una temporada completa (caso real ya documentado de Tercera FEB
       antes de 2026-27).
    4. Se desplaza (shift(1)) esa media por equipo, ordenada
       cronologicamente -- la temporada N recibe la media de la N-1 (o
       NaN si la N-1 no existe o quedo descartada en el paso 3).
    """
    season_meta = (
        df.groupby(["source", "team_id", "season"])
        .agg(season_start=("date_parsed", "min"), n_games=("date_parsed", "size"),
             **{col: (col, "mean") for col in stat_cols})
        .reset_index()
        .sort_values(["source", "team_id", "season_start"])
    )
    # Solo temporadas elegibles (>= min_prior_season_games) "cuentan"
    # como fuente de arrastre -- las que no, se tratan como huecos (NaN),
    # nunca como un cero, para no contaminar la temporada siguiente con
    # el promedio de una fase final/promocion pequeña.
    eligible = season_meta["n_games"] >= min_prior_season_games
    for col in stat_cols:
        eligible_val = season_meta[col].where(eligible)
        season_meta[f"prior_season_{col}"] = (
            eligible_val.groupby([season_meta["source"], season_meta["team_id"]]).shift(1)
        )

    prior_cols = ["source", "team_id", "season"] + [f"prior_season_{c}" for c in stat_cols]
    return df.merge(season_meta[prior_cols], on=["source", "team_id", "season"], how="left")


def _add_pregame_rolling(
    team_games: pd.DataFrame, stat_cols: list[str] = ROLLING_STAT_COLS
) -> pd.DataFrame:
    """Añade `<col>_pre` = media de `col` en los partidos ANTERIORES del
    mismo equipo, en la misma temporada y fuente -- shift(1) ANTES de la
    media expanding, para excluir siempre el partido que se esta
    calculando (si no se hiciera este shift, el propio partido entraria
    en su propia media y volveriamos a tener fuga de datos).

    ARRASTRE ENTRE TEMPORADAS (AÑADIDO 2026-09-21, ver
    _add_season_carryover() para la verificacion empirica completa):
    cuando el equipo tiene una temporada anterior real y con volumen
    suficiente (>= MIN_PRIOR_SEASON_GAMES) en la misma fuente,
    `<col>_pre` deja de ser SOLO la media de esta temporada -- se encoge
    (shrinkage bayesiano, mismo patron que SHOT_PRIOR_K_SHRINK en
    src/features.py) hacia la media de la temporada anterior con
    SEASON_PRIOR_K_SHRINK partidos virtuales:
        pre = (suma_actual + k * media_temporada_anterior)
              / (n_prior_games + k)
    En el primer partido de una temporada nueva (n_prior_games=0) esto
    deja `<col>_pre` = la media de la temporada anterior EN VEZ de NaN
    -- antes de esto, el modelo arrancaba cada temporada a ciegas (por
    eso build_pregame_game_dataset() descartaba con min_prior_games esos
    partidos de entrada). Sin temporada anterior valida (equipo nuevo,
    o la anterior era una fase final pequeña), `<col>_pre` se queda
    exactamente como antes (NaN hasta acumular partidos de verdad).

    También añade:
    - n_prior_games: cuantos partidos previos tiene ya el equipo esta
      temporada (0 en el primero) -- sigue siendo SOLO de esta
      temporada (no cuenta el arrastre), se usa para decidir si hace
      falta o no el arrastre (build_pregame_game_dataset()) y para
      reportar cuanto historial hay.
    - used_season_carryover: True si `<col>_pre` de esa fila viene del
      encogido de arriba (n_prior_games bajo Y temporada anterior
      valida disponible) -- solo para diagnostico/reporting, no es una
      feature del modelo.
    - rest_days: dias desde el partido anterior del equipo (NaN en el
      primer partido de la temporada, arrastre entre temporadas o no --
      el descanso real SI depende de la fecha del ultimo partido
      jugado, y eso no se puede "arrastrar" de una temporada a otra).
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

    df = _add_season_carryover(df, stat_cols)
    df["used_season_carryover"] = False
    for col in stat_cols:
        prior_col = f"prior_season_{col}"
        has_prior = df[prior_col].notna()
        cur_sum = df[f"{col}_pre"].fillna(0) * df["n_prior_games"]
        blended = (cur_sum + SEASON_PRIOR_K_SHRINK * df[prior_col]) / (df["n_prior_games"] + SEASON_PRIOR_K_SHRINK)
        df[f"{col}_pre"] = np.where(has_prior, blended, df[f"{col}_pre"])
        df["used_season_carryover"] = df["used_season_carryover"] | has_prior
    df = df.drop(columns=[f"prior_season_{c}" for c in stat_cols])
    return df


def build_pregame_game_dataset(
    conn: sqlite3.Connection,
    sources: list[str] | None = None,
    trainable_only: bool = True,
    min_prior_games: int = 5,
) -> pd.DataFrame:
    """Una fila = un partido (no un equipo-partido). Features PRE-partido
    de local y visitante (medias de temporada hasta ANTES de este
    partido, con arrastre de la temporada anterior si hay -- ver
    _add_season_carryover()), su diferencia local-visitante (`diff_*_pre`,
    la forma que suele darle mas señal a un modelo lineal que los dos
    valores sueltos), descanso de cada equipo, y el target `home_win`.

    Descarta un partido si el local o el visitante NO tienen suficiente
    informacion pre-partido de ninguna de las dos formas: ni
    `min_prior_games` partidos ya jugados ESTA temporada, ni arrastre
    valido de la temporada anterior (`used_season_carryover`, ver
    _add_pregame_rolling()) -- antes de añadir el arrastre esto
    descartaba TODOS los primeros `min_prior_games` partidos de cada
    equipo cada temporada; ahora solo descarta los que de verdad no
    tienen ninguna informacion detras (equipos nuevos, o cuya temporada
    anterior fue una fase final/promocion demasiado pequeña).

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

    pre_cols = [f"{c}_pre" for c in ROLLING_STAT_COLS] + ["n_prior_games", "rest_days", "used_season_carryover"]

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

    home_has_info = (games["home_n_prior_games"] >= min_prior_games) | games["home_used_season_carryover"]
    away_has_info = (games["away_n_prior_games"] >= min_prior_games) | games["away_used_season_carryover"]
    games = games[home_has_info & away_has_info].reset_index(drop=True)

    return games.sort_values(["source", "date"]).reset_index(drop=True)
