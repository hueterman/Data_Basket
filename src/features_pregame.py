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
from src.pipeline.clubs import apply_club_ids

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


# ---------------------------------------------------------------------------
# Rating ELO opponent-adjusted (AÑADIDO 2026-09-21)
#
# MOTIVACION: diff_win_pre (arriba) es el % de victorias del equipo esta
# temporada, pero NO ajusta por la calidad de los rivales a los que se
# enfrento -- un 8-2 contra rivales flojos no es lo mismo que un 8-2
# contra los mejores del grupo. Se probaron primero dos alternativas MAS
# SIMPLES (sesion 2026-09-21, ver historial de commits) y NINGUNA de las
# dos aporto señal real en un split cronologico igual al de train.py:
# - Prior de enfrentamiento directo (head-to-head), mismo patron de
#   shrinkage que SHOT_PRIOR_K_SHRINK: roc_auc 0.7406 -> 0.7408-0.7409
#   (ruido).
# - "Fuerza de calendario" de un solo salto (media del win_pre de los
#   rivales afrontados esta temporada): roc_auc 0.7403 -> 0.7400 (peor).
# Un rating ELO ajusta por rival de forma ITERATIVA (la fuerza de un
# equipo se deriva de a quien ha ganado/perdido, y la fuerza de esos
# rivales de los suyos, encadenado sobre TODO el historial, no solo la
# temporada actual) -- SI aporta señal real, verificado en el mismo
# split cronologico sobre los 18180 partidos utilizables de las fuentes
# entrenables: roc_auc 0.7406 -> 0.7481, log_loss 0.5901 -> 0.5835,
# brier 0.2029 -> 0.1999 (con K=20, multiplicador de margen de victoria,
# regresion del 50% a la media entre temporadas -- ver constantes mas
# abajo). Correlacion con diff_win_pre = 0.887 -- alta pero no perfecta,
# hay señal incremental real, no es un duplicado.
#
# COMO SE CALCULA (replay secuencial, nunca fuga de datos -- el rating
# de un equipo ANTES de un partido solo depende de partidos con fecha
# ANTERIOR, por construccion del propio algoritmo, igual que un
# shift(1).expanding() pero recursivo en vez de una media):
# 1. Cada equipo nuevo en una fuente arranca en ELO_BASE.
# 2. Tras cada partido, se actualiza el rating de ambos equipos segun la
#    formula ELO estandar (probabilidad esperada de ganar segun la
#    diferencia de rating, con una ventaja de jugar en casa -- ver
#    _source_home_advantage_elo()) mas un multiplicador de margen de
#    victoria adaptado de la metodologia ELO de NBA de FiveThirtyEight
#    (un partido ganado por mas puntos mueve mas el rating, amortiguado
#    para que una diferencia de rating ya grande no infle aun mas al
#    favorito por ganar "como se esperaba"): 538 no publica un paper
#    formal de esto, es su formula de referencia, adoptada aqui tal
#    cual y verificada empiricamente arriba, no una novedad propia.
# 3. Al cambiar de temporada, el rating de cada equipo se REGRESA una
#    fraccion (ELO_REGRESS_TO_MEAN) hacia ELO_BASE -- roster distinto
#    temporada a temporada, mismo espiritu que SEASON_PRIOR_K_SHRINK
#    pero como regresion a la media en vez de encogido bayesiano (el
#    ELO ya es un unico numero acumulado, no una media de partidos que
#    encoger).
#
# LIMITACIONES CONOCIDAS, no resueltas a proposito (alcance de esta
# sesion): el punto de partida ELO_BASE para un equipo completamente
# nuevo en una fuente no tiene en cuenta el nivel real de esa fuente
# (un ascendido de una division inferior no entra con un rating mas
# bajo); K/regresion se fijaron por valores redondos convencionales
# (20 y 0.5) tras comprobar que el resultado es estable en un rango
# razonable (K=10-40, regresion 0.25-0.5 dan roc_auc entre 0.745 y
# 0.748), no por busqueda exhaustiva -- mismo criterio que el resto de
# hiperparametros del proyecto (ver train.py).
ELO_BASE = 1500.0
ELO_K = 20.0
ELO_REGRESS_TO_MEAN = 0.5

# Ventaja de jugar en casa (ver _source_home_advantage_elo()): encogido
# bayesiano hacia esta tasa global (verificada en datos reales -- %
# de victorias del local en las 12 fuentes entrenables con marcador,
# 19970 partidos) con ELO_HOME_ADV_SHRINK_K partidos virtuales.
ELO_HOME_ADV_GLOBAL_FALLBACK_RATE = 0.580
ELO_HOME_ADV_SHRINK_K = 300.0


def _source_home_advantage_elo(
    games_ordered: pd.DataFrame, k_shrink: float = ELO_HOME_ADV_SHRINK_K,
    fallback_rate: float = ELO_HOME_ADV_GLOBAL_FALLBACK_RATE,
) -> dict[str, float]:
    """Ventaja de jugar en casa, en puntos ELO, por `source` -- necesaria
    para que la probabilidad "esperada" de cada partido en _replay_elo()
    no este sesgada. VERIFICADO en datos reales (no adivinado): el % de
    victorias del local varia mucho entre fuentes, de 0.55 (nba) a 0.63
    (euroleague) en las fuentes con volumen real, con fuentes pequeñas
    (nextgen/copaespaa/tercerafeb, <=150 partidos) dando cifras poco
    fiables (0.30-0.51 -- ruido de muestra pequeña, copaespaa es ademas
    una copa con eliminacion directa, "local" pesa distinto ahi). Una
    ventaja global unica sesgaria el ELO de las fuentes con ventaja real
    distinta a la media, asi que se encoge el % de victorias del local
    de cada fuente hacia `fallback_rate` (el global, 0.580) con
    `k_shrink` partidos virtuales -- fuentes grandes (nba, acb...) casi
    no se mueven de su propio numero, las pequeñas quedan casi en el
    global. Se convierte a puntos ELO con la formula estandar (inversa
    de la probabilidad esperada ELO): 400*log10(p/(1-p)).
    """
    by_source = games_ordered.groupby("source")["home_win"].agg(["sum", "size"])
    shrunk_rate = (by_source["sum"] + k_shrink * fallback_rate) / (by_source["size"] + k_shrink)
    elo_points = 400.0 * np.log10(shrunk_rate / (1.0 - shrunk_rate))
    return elo_points.to_dict()


def _replay_elo(
    games_ordered: pd.DataFrame, k: float = ELO_K, regress_to_mean: float = ELO_REGRESS_TO_MEAN,
) -> tuple[np.ndarray, np.ndarray, dict[tuple[str, str], float]]:
    """Nucleo del rating ELO -- un unico paso secuencial sobre
    `games_ordered` (una fila por partido REAL, ya ordenada por
    [source, date_parsed, game_id], con columnas source/season/
    home_team_id/away_team_id/home_score/away_score). Para cada
    partido, en orden:
    1. Registra el rating ACTUAL (antes de este partido) de local y
       visitante en `home_pre`/`away_pre` -- lo que se devuelve como
       feature, nunca el rating ya actualizado con el resultado de este
       mismo partido (eso seria fuga de datos).
    2. Si a alguno de los dos equipos le cambio la temporada desde su
       partido anterior, regresa su rating una fraccion hacia
       ELO_BASE (ver docstring del modulo).
    3. Actualiza ambos ratings con el resultado real de este partido
       (formula ELO estandar + multiplicador de margen de victoria).

    Devuelve (home_pre, away_pre) -- arrays alineados con
    `games_ordered` -- y el diccionario de ratings FINAL (tras el
    ultimo partido de cada equipo), que es lo que necesita una
    prediccion EN VIVO (src/predict.py) para el rating "a dia de hoy".
    """
    games_ordered = games_ordered.copy()
    games_ordered["home_win"] = (games_ordered["home_score"] > games_ordered["away_score"]).astype(int)
    home_adv_by_source = _source_home_advantage_elo(games_ordered)

    elo: dict[tuple[str, str], float] = {}
    last_season: dict[tuple[str, str], str] = {}
    home_pre = np.empty(len(games_ordered))
    away_pre = np.empty(len(games_ordered))

    for i, row in enumerate(games_ordered.itertuples(index=False)):
        kh, ka = (row.source, row.home_team_id), (row.source, row.away_team_id)
        if kh not in elo:
            elo[kh] = ELO_BASE
        if ka not in elo:
            elo[ka] = ELO_BASE
        if kh in last_season and last_season[kh] != row.season:
            elo[kh] += regress_to_mean * (ELO_BASE - elo[kh])
        if ka in last_season and last_season[ka] != row.season:
            elo[ka] += regress_to_mean * (ELO_BASE - elo[ka])
        last_season[kh] = row.season
        last_season[ka] = row.season

        eh, ea = elo[kh], elo[ka]
        home_pre[i] = eh
        away_pre[i] = ea

        home_adv = home_adv_by_source.get(row.source, 0.0)
        expected_home = 1.0 / (1.0 + 10 ** ((ea - eh - home_adv) / 400.0))
        actual_home = float(row.home_win)
        margin = abs(row.home_score - row.away_score)
        winner_elo = eh if actual_home == 1.0 else ea
        loser_elo = ea if actual_home == 1.0 else eh
        # Multiplicador de margen de victoria adaptado de la metodologia
        # ELO de NBA de FiveThirtyEight -- amortigua el impacto de una
        # diferencia de rating ya grande para que el favorito no siga
        # inflandose por ganar "como tocaba".
        mov_mult = ((margin + 3) ** 0.8) / (7.5 + 0.006 * (winner_elo - loser_elo))

        delta = k * mov_mult * (actual_home - expected_home)
        elo[kh] = eh + delta
        elo[ka] = ea - delta

    return home_pre, away_pre, elo


def _one_row_per_game(team_games: pd.DataFrame) -> pd.DataFrame:
    """Colapsa `team_games` (una fila por EQUIPO-partido, cada partido
    real aparece dos veces) a una fila por partido REAL -- necesario
    antes de _replay_elo() para no procesar cada partido dos veces.
    `team_games` debe traer ya `is_home` (bool) y `date_parsed`
    (mismo parseo que _add_pregame_rolling) añadidos por quien llama."""
    out = team_games.loc[
        team_games["is_home"],
        ["source", "game_id", "season", "date_parsed", "team_id", "opp_id", "points_for", "points_against"],
    ].rename(columns={
        "team_id": "home_team_id", "opp_id": "away_team_id",
        "points_for": "home_score", "points_against": "away_score",
    })
    return out.sort_values(["source", "date_parsed", "game_id"]).reset_index(drop=True)


def add_elo_ratings(team_games: pd.DataFrame) -> pd.DataFrame:
    """Añade `elo_pre` a `team_games` (rating ELO de cada equipo ANTES
    de cada uno de sus partidos, ver _replay_elo()) -- version de
    ENTRENAMIENTO, usada por build_pregame_game_dataset(). `team_games`
    debe traer ya `is_home`/`date_parsed` (ver _one_row_per_game())."""
    one_game = _one_row_per_game(team_games)
    home_pre, away_pre, _final = _replay_elo(one_game)
    one_game["home_elo_pre"] = home_pre
    one_game["away_elo_pre"] = away_pre

    home_side = one_game[["source", "game_id", "home_team_id", "home_elo_pre"]].rename(
        columns={"home_team_id": "team_id", "home_elo_pre": "elo_pre"})
    away_side = one_game[["source", "game_id", "away_team_id", "away_elo_pre"]].rename(
        columns={"away_team_id": "team_id", "away_elo_pre": "elo_pre"})
    elo_long = pd.concat([home_side, away_side], ignore_index=True)

    return team_games.merge(elo_long, on=["source", "game_id", "team_id"], how="left")


def current_elo_ratings(team_games: pd.DataFrame) -> dict[tuple[str, str], float]:
    """Rating ELO ACTUAL (tras el ultimo partido jugado) de cada
    (source, team_id) en `team_games` -- version EN VIVO, usada por
    src/predict.py para una prediccion de un partido que todavia no se
    ha jugado (a diferencia de add_elo_ratings(), que da el rating
    ANTES de cada partido ya jugado). `team_games` debe traer ya
    `is_home`/`date_parsed` (ver _one_row_per_game())."""
    one_game = _one_row_per_game(team_games)
    _home_pre, _away_pre, final_ratings = _replay_elo(one_game)
    return final_ratings


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
    Tambien incluye `margin` (home_score - away_score, AÑADIDO 2026-09-22,
    ver train_margin.py) -- positivo si gana el local, calculado por
    separado de `home_win` (no se deriva uno del otro).

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
    games_df = pd.read_sql(
        "SELECT source, game_id, home_team_id, home_score, away_score FROM games", conn
    )
    games_df = apply_club_ids(conn, games_df, cols=("home_team_id",))  # mismos ids que team_games
    pre = pre.merge(
        games_df.drop(columns=["home_score", "away_score"]), on=["source", "game_id"], how="left"
    )
    pre["is_home"] = pre["team_id"] == pre["home_team_id"]
    pre = add_elo_ratings(pre)

    pre_cols = [f"{c}_pre" for c in ROLLING_STAT_COLS] + ["n_prior_games", "rest_days", "used_season_carryover", "elo_pre"]

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
    games = games.merge(
        games_df[["source", "game_id", "home_score", "away_score"]],
        on=["source", "game_id"], how="left",
    )
    # AÑADIDO 2026-09-22, ver train_margin.py: margin > 0 = gana el
    # local, coherente con home_win pero calculado por separado (no se
    # deriva uno del otro).
    games["margin"] = games["home_score"] - games["away_score"]

    for c in ROLLING_STAT_COLS:
        games[f"diff_{c}_pre"] = games[f"home_{c}_pre"] - games[f"away_{c}_pre"]
    games["diff_rest_days"] = games["home_rest_days"] - games["away_rest_days"]
    games["diff_elo_pre"] = games["home_elo_pre"] - games["away_elo_pre"]

    home_has_info = (games["home_n_prior_games"] >= min_prior_games) | games["home_used_season_carryover"]
    away_has_info = (games["away_n_prior_games"] >= min_prior_games) | games["away_used_season_carryover"]
    games = games[home_has_info & away_has_info].reset_index(drop=True)

    return games.sort_values(["source", "date"]).reset_index(drop=True)
