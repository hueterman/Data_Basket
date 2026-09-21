"""Primer conjunto de features para train.py (Fase 5 del plano),
construido sobre la base de datos normalizada (data/processed/basketball.db)
y sobre las features ya validadas en notebooks/01_eda_features.ipynb
(ritmo/eficiencia por posesion, secciones 2 y 5; coordenadas normalizadas
a metros, secciones 3/5/6).

Filtro de entrenabilidad (obligatorio, ver plano seccion 10 y
src/scraper/base.py): por defecto SOLO se calculan features para
TRAINABLE_SOURCES -- WNBA y CBB quedan fuera de estas funciones aunque
esten en la base de datos (sus terminos de uso prohiben usar sus datos
para entrenar modelos de IA). Pasar `sources=` explicitamente para
calcular features de EDA sobre cualquier fuente (p. ej. WNBA), pero
NUNCA usar ese resultado para entrenar.

Dos tablas de features, no una: el ritmo/eficiencia vive a nivel
equipo-partido (una fila = un equipo en un partido concreto) y las
features espaciales viven a nivel tiro (una fila = un tiro concreto) --
mezclarlas en una sola tabla obligaria a agregar una de las dos sin
necesidad. train.py decide como usarlas (una fila por partido, por
tiro, o agregando tiros de vuelta a nivel equipo-partido) segun el
modelo concreto que construya.
"""
from __future__ import annotations

import math
import sqlite3

import numpy as np
import pandas as pd

from src.scraper.base import TRAINABLE_SOURCES

# ---------------------------------------------------------------------------
# Calibracion de coordenadas de tiro por fuente -- misma logica ya validada
# en el notebook (secciones 3 y 5), reunida aqui para no duplicarla entre
# el notebook y train.py. Cada entrada da como convertir loc_x/loc_y
# (unidad nativa del scraper) a metros con origen en el aro, y la
# distancia real (en metros) de la linea de 3 -- general y de esquina --
# de esa competicion, para poder clasificar zona de tiro sin adivinar.
#
# NBA: decimas de pie, documentado en el propio scraper (nba.py).
# Euroliga/EuroCup: centimetros -- hipotesis confirmada empiricamente en
#   el notebook (hueco de densidad justo en 6.75m, seccion 6).
# WNBA: pixeles sobre el SVG de basketball-reference, calibrados con dos
#   constantes fisicas reales (ancho de pista 50ft, aro a 5.25ft de la
#   linea de fondo) y confirmados igual con el hueco de densidad
#   (notebook, seccion 5) -- WNBA es NON_TRAINABLE, se deja aqui solo
#   por si se pide EDA explicito de sus tiros con `sources=["wnba"]`.
_WNBA_X0_PX = 488 / 2
_WNBA_SCALE_PX_PER_FT = 488 / 50.0
_WNBA_Y0_PX = 5.25 * _WNBA_SCALE_PX_PER_FT
_FT_TO_M = 0.3048

SHOT_COORD_CALIBRATION: dict[str, dict] = {
    "nba": {
        "to_meters": lambda x, y: (x / 10 * _FT_TO_M, y / 10 * _FT_TO_M),
        "three_pt_arc_m": 7.24,     # 23'9"
        "three_pt_corner_m": 6.706,  # 22'
    },
    "euroleague": {
        "to_meters": lambda x, y: (x / 100, y / 100),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "eurocup": {
        "to_meters": lambda x, y: (x / 100, y / 100),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "nextgen": {
        # Mismo proveedor de datos que euroleague/eurocup (live.euroleague.net
        # /api/Points, mismo formato COORD_X/COORD_Y) y mismas reglas FIBA de
        # cancha para el arco de 3 (el ANGT es sub-18 pero FIBA no tiene una
        # linea de 3 distinta para categorias juveniles) -- confirmado en
        # vivo el 2026-09-19: un 3FGA fallado real (partido JTB23_16) dio
        # COORD_X=-671, que encaja con el radio de 6.75m*100=675 casi exacto.
        "to_meters": lambda x, y: (x / 100, y / 100),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "wnba": {
        "to_meters": lambda x, y: (
            (x - _WNBA_X0_PX) / _WNBA_SCALE_PX_PER_FT * _FT_TO_M,
            (y - _WNBA_Y0_PX) / _WNBA_SCALE_PX_PER_FT * _FT_TO_M,
        ),
        "three_pt_arc_m": 6.750,   # 22'1.75", uniforme (sin variar en la esquina)
        "three_pt_corner_m": 6.750,
    },
    # acb: sin datos de tiro todavia -- acb.py no implementa get_shots().
    #
    # feb / lfendesa / lfchallenge / lf2 / segundafeb / tercerafeb /
    # copaespaa (FebSource, ver feb.py) -- CALIBRADO 2026-09-20, misma
    # sesion en la que se corrigieron dos bugs reales de get_shots()
    # (ver su docstring): loc_x/loc_y NO son pixeles ni cm como el resto
    # de fuentes, son PORCENTAJE (0-100) de la CANCHA COMPLETA -- x a lo
    # largo del eje longitudinal (confirmado por su histograma bimodal:
    # dos nubes densas, una hacia x~5-10 y otra hacia x~85-95, con un
    # valle en el centro x~35-60 donde casi nadie tira -- exactamente lo
    # esperable con dos aros en los dos extremos de una cancha completa),
    # y a lo largo del eje de anchura (histograma unimodal centrado en
    # y~50, ambos aros centrados en el ancho de la cancha).
    #
    # Conversion: se pliega x al aro mas cercano (min(x, 100-x)) y se usa
    # la cancha reglamentaria FIBA (28m x 15m, aro a 1.575m de la linea
    # de fondo -- normativa oficial, no una fuente que haga falta
    # verificar en vivo, igual que 6.75m/6.60m de linea de 3 ya se usa
    # sin verificacion adicional para euroleague/eurocup/nextgen).
    # CONFIRMADO empiricamente con densidad de tiros reales de 'feb'
    # (186986 tiros): convertida asi, la distancia al aro tiene el
    # patron esperado de cualquier grafico de tiro real -- pico enorme
    # cerca del aro (0-2m), valle claro justo antes de la linea de 3
    # (minimo en el bin de 6.00-6.25m), y la cuenta vuelve a subir con
    # fuerza a partir de 6.5-6.75m hasta un segundo pico en 7.5-8.5m
    # (encaja con 6.75m de linea de 3 FIBA + el patron habitual de tirar
    # algo mas atras del minimo de la linea).
    "feb": {
        "to_meters": lambda x, y: (
            (y / 100.0) * 15.0 - 7.5,
            28.0 * np.minimum(x, 100 - x) / 100.0 - 1.575,
        ),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "lfendesa": {
        "to_meters": lambda x, y: (
            (y / 100.0) * 15.0 - 7.5,
            28.0 * np.minimum(x, 100 - x) / 100.0 - 1.575,
        ),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "lfchallenge": {
        "to_meters": lambda x, y: (
            (y / 100.0) * 15.0 - 7.5,
            28.0 * np.minimum(x, 100 - x) / 100.0 - 1.575,
        ),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "lf2": {
        "to_meters": lambda x, y: (
            (y / 100.0) * 15.0 - 7.5,
            28.0 * np.minimum(x, 100 - x) / 100.0 - 1.575,
        ),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "segundafeb": {
        "to_meters": lambda x, y: (
            (y / 100.0) * 15.0 - 7.5,
            28.0 * np.minimum(x, 100 - x) / 100.0 - 1.575,
        ),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "tercerafeb": {
        "to_meters": lambda x, y: (
            (y / 100.0) * 15.0 - 7.5,
            28.0 * np.minimum(x, 100 - x) / 100.0 - 1.575,
        ),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    "copaespaa": {
        "to_meters": lambda x, y: (
            (y / 100.0) * 15.0 - 7.5,
            28.0 * np.minimum(x, 100 - x) / 100.0 - 1.575,
        ),
        "three_pt_arc_m": 6.75,
        "three_pt_corner_m": 6.60,
    },
    # IMPORTANTE -- calibrar coordenadas NO es lo mismo que tener datos
    # limpios: las filas de `shots` YA guardadas para estas 7
    # competiciones vienen del codigo con el bug de made/shot_type
    # (ver "BUGS REALES corregidos 2026-09-20(e)" en feb.py) y no se
    # pueden arreglar sin reingerir. Por eso train_shot.py NO las
    # incluye todavia pese a estar aqui ya calibradas -- ver
    # FEB_SHOTS_PENDING_REINGEST en su cabecera.
}


def _require_known_sources(present: set[str], calibrated: set[str]) -> None:
    """Avisa y no adivina: si aparece una fuente con tiros para la que
    no hay calibracion todavia, mejor un error explicito que normalizar
    con un supuesto sin confirmar."""
    unknown = present - calibrated
    if unknown:
        raise NotImplementedError(
            f"Sin calibracion de coordenadas de tiro para: {sorted(unknown)} "
            f"-- añade su entrada a SHOT_COORD_CALIBRATION (ver notebook, "
            f"secciones 3/5/6) antes de pedir features de tiro para "
            f"esa fuente."
        )


def _resolve_sources(conn: sqlite3.Connection, sources: list[str] | None,
                      trainable_only: bool) -> list[str]:
    if sources is not None:
        return list(sources)
    available = {
        r[0] for r in conn.execute("SELECT DISTINCT source FROM games").fetchall()
    }
    if trainable_only:
        available &= TRAINABLE_SOURCES
    return sorted(available)


# ---------------------------------------------------------------------------
# Features equipo-partido: ritmo y los "cuatro factores" de Dean Oliver
# (eFG%, TOV%, ORB%, FT rate) -- el conjunto de eficiencia por posesion
# mas estandar en analitica de baloncesto, calculado con formulas
# publicas conocidas (no inventadas), sobre columnas reales de
# team_stats/games/player_stats.
# ---------------------------------------------------------------------------

def build_team_game_features(
    conn: sqlite3.Connection,
    sources: list[str] | None = None,
    trainable_only: bool = True,
) -> pd.DataFrame:
    """Una fila = un equipo en un partido concreto ya jugado.

    Columnas: identificacion (source/game_id/season/date/team_id/opp_id),
    resultado (points_for/points_against/win), ritmo (poss/team_minutes/
    pace_40) y los cuatro factores (efg_pct/tov_pct/orb_pct/ft_rate) mas
    ortg/drtg. `trainable_only=True` (por defecto) filtra a
    TRAINABLE_SOURCES -- pasar False solo para EDA, nunca para construir
    datos de entrenamiento.
    """
    src_list = _resolve_sources(conn, sources, trainable_only)
    if not src_list:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in src_list)

    games_df = pd.read_sql(
        f"SELECT * FROM games WHERE played = 1 AND source IN ({placeholders})",
        conn, params=src_list,
    )
    team_stats_df = pd.read_sql(
        f"SELECT * FROM team_stats WHERE source IN ({placeholders})",
        conn, params=src_list,
    )
    player_stats_df = pd.read_sql(
        f"SELECT source, game_id, team_id, minutes FROM player_stats "
        f"WHERE source IN ({placeholders})",
        conn, params=src_list,
    )
    if games_df.empty or team_stats_df.empty:
        return pd.DataFrame()

    team_minutes = (
        player_stats_df.groupby(["source", "game_id", "team_id"])["minutes"].sum() / 5
    ).rename("team_minutes").reset_index()

    home = games_df[["source", "game_id", "season", "date", "home_team_id",
                      "away_team_id", "home_score", "away_score"]].rename(
        columns={"home_team_id": "team_id", "away_team_id": "opp_id",
                 "home_score": "points_for", "away_score": "points_against"}
    )
    away = games_df[["source", "game_id", "season", "date", "home_team_id",
                      "away_team_id", "home_score", "away_score"]].rename(
        columns={"away_team_id": "team_id", "home_team_id": "opp_id",
                 "away_score": "points_for", "home_score": "points_against"}
    )
    team_games = pd.concat([home, away], ignore_index=True)

    feats = team_games.merge(team_stats_df, on=["source", "game_id", "team_id"], how="left")
    feats = feats.merge(team_minutes, on=["source", "game_id", "team_id"], how="left")

    feats["fga"] = feats["t2_att"] + feats["t3_att"]
    feats["fgm"] = feats["t2_made"] + feats["t3_made"]
    feats["poss"] = feats["fga"] - feats["reb_off"] + feats["turnovers"] + 0.44 * feats["ft_att"]

    # Rebotes defensivos del rival, necesarios para ORB% (formula real:
    # ORB / (ORB + DRB_rival)) -- se cruzan por (source, game_id, opp_id).
    opp_cols = feats[["source", "game_id", "team_id", "poss", "reb_def"]].rename(
        columns={"team_id": "opp_id", "poss": "opp_poss", "reb_def": "opp_reb_def"}
    )
    feats = feats.merge(opp_cols, on=["source", "game_id", "opp_id"], how="left")

    feats["win"] = (feats["points_for"] > feats["points_against"]).astype(int)
    feats["pace_40"] = feats["poss"] * 40 / feats["team_minutes"]
    feats["ortg"] = 100 * feats["points_for"] / feats["poss"]
    feats["drtg"] = 100 * feats["points_against"] / feats["opp_poss"]

    # Cuatro factores (Dean Oliver) -- formulas estandar publicas:
    feats["efg_pct"] = (feats["fgm"] + 0.5 * feats["t3_made"]) / feats["fga"]
    feats["tov_pct"] = 100 * feats["turnovers"] / (feats["fga"] + 0.44 * feats["ft_att"] + feats["turnovers"])
    feats["orb_pct"] = 100 * feats["reb_off"] / (feats["reb_off"] + feats["opp_reb_def"])
    feats["ft_rate"] = feats["ft_att"] / feats["fga"]  # FTA/FGA (convencion basketball-reference)

    cols = ["source", "game_id", "season", "date", "team_id", "opp_id",
            "points_for", "points_against", "win",
            "poss", "team_minutes", "pace_40", "ortg", "drtg",
            "efg_pct", "tov_pct", "orb_pct", "ft_rate"]
    return feats[cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Features de tiro: coordenadas normalizadas a metros + distancia/angulo +
# zona (agrupamiento espacial simplificado por anillos de distancia,
# separando triple de esquina vs. de arco con la distancia real de 3 de
# cada competicion -- ver SHOT_COORD_CALIBRATION). No es la taxonomia
# oficial de zonas de nba_api (que usa el area pintada, no solo
# distancia) -- es una simplificacion deliberada para poder aplicarla
# igual a cualquier fuente con coordenadas ya calibradas, documentada
# como tal para que train.py no la confunda con la oficial.
# ---------------------------------------------------------------------------

_RIM_M = 1.22       # "restricted area" NBA (4 ft), usado como referencia universal
_SHORT_MID_M = 3.05  # 10 ft
_ZONES = ["aro", "pintura_corta", "media_larga", "triple_esquina", "triple_arco"]


def _classify_zone(dist_m: float, x_m: float, three_arc: float, three_corner: float) -> str:
    if dist_m < _RIM_M:
        return "aro"
    if dist_m < _SHORT_MID_M:
        return "pintura_corta"
    # Umbral de triple real de esta fuente: en la banda de esquina (|x|
    # grande, cerca de la linea de fondo) el triple empieza antes
    # (three_corner) que en el arco (three_arc) -- se aproxima "banda de
    # esquina" como |x| > three_corner, igual que el tramo recto real de
    # la linea de 3 en NBA/FIBA (ver draw_court()/draw_court_fiba() del
    # notebook).
    is_corner_band = abs(x_m) > three_corner
    threshold = three_corner if is_corner_band else three_arc
    if dist_m < threshold:
        return "media_larga"
    return "triple_esquina" if is_corner_band else "triple_arco"


def build_shot_features(
    conn: sqlite3.Connection,
    sources: list[str] | None = None,
    trainable_only: bool = True,
) -> pd.DataFrame:
    """Una fila = un tiro. Añade loc_x_m/loc_y_m (metros, origen en el
    aro), dist_m, angle_deg (0 = de frente al aro, 90 = desde la banda) y
    zone (ver `_ZONES`). `trainable_only=True` (por defecto) filtra a
    TRAINABLE_SOURCES -- pasar False solo para EDA (p. ej. WNBA), nunca
    para construir datos de entrenamiento.
    """
    src_list = _resolve_sources(conn, sources, trainable_only)
    if not src_list:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in src_list)

    shots = pd.read_sql(
        f"SELECT * FROM shots WHERE source IN ({placeholders})",
        conn, params=src_list,
    )
    if shots.empty:
        return shots

    present = set(shots["source"].unique())
    _require_known_sources(present, set(SHOT_COORD_CALIBRATION))

    loc_x_m = np.empty(len(shots))
    loc_y_m = np.empty(len(shots))
    for source, calib in SHOT_COORD_CALIBRATION.items():
        mask = (shots["source"] == source).to_numpy()
        if not mask.any():
            continue
        x_m, y_m = calib["to_meters"](shots.loc[mask, "loc_x"], shots.loc[mask, "loc_y"])
        loc_x_m[mask] = x_m
        loc_y_m[mask] = y_m

    shots = shots.copy()
    shots["loc_x_m"] = loc_x_m
    shots["loc_y_m"] = loc_y_m
    shots["dist_m"] = np.hypot(shots["loc_x_m"], shots["loc_y_m"])
    # angle_deg: 0 = tiro de frente (sobre el eje del aro), 90 = desde la
    # banda -- atan2(|x|, y) en grados, recortado a [0, 180] para tiros
    # detras de la linea de fondo (y negativo, caso raro pero real).
    shots["angle_deg"] = np.degrees(np.arctan2(shots["loc_x_m"].abs(), shots["loc_y_m"]))

    three_arc = shots["source"].map(lambda s: SHOT_COORD_CALIBRATION[s]["three_pt_arc_m"])
    three_corner = shots["source"].map(lambda s: SHOT_COORD_CALIBRATION[s]["three_pt_corner_m"])
    shots["zone"] = [
        _classify_zone(d, x, ta, tc)
        for d, x, ta, tc in zip(shots["dist_m"], shots["loc_x_m"], three_arc, three_corner)
    ]

    cols = ["source", "game_id", "player_id", "team_id", "loc_x", "loc_y",
            "loc_x_m", "loc_y_m", "dist_m", "angle_deg", "zone", "made", "shot_type"]
    return shots[cols].reset_index(drop=True)


def zone_fg_pct(shot_features: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    """% de acierto por zona (y opcionalmente por otra columna, p. ej.
    ["source"] o ["source", "team_id"]) -- agregado directo de
    build_shot_features(), util como feature de equipo/jugador a nivel
    partido (volumen y acierto por zona) o como comprobacion de cordura
    rapida (la zona "aro" deberia rondar 60-65%, "triple_arco" 33-38%,
    en cualquier fuente con datos reales)."""
    group_cols = (by or []) + ["zone"]
    out = shot_features.groupby(group_cols).agg(
        intentos=("made", "size"),
        aciertos=("made", "sum"),
    )
    out["fg_pct"] = out["aciertos"] / out["intentos"]
    return out.reset_index()


# Peso (en intentos "virtuales" de liga) del encogido bayesiano de
# add_shot_priors() -- compartido con src/predict.py para que el prior
# calculado EN VIVO en una prediccion use exactamente la misma formula
# que el usado al entrenar (train_shot.py), nunca dos constantes que
# puedan divergir.
SHOT_PRIOR_K_SHRINK = 20.0


def add_shot_priors(
    shots: pd.DataFrame, games: pd.DataFrame, k_shrink: float = SHOT_PRIOR_K_SHRINK,
) -> pd.DataFrame:
    """Añade `player_prior_fg` y `player_prior_n_log` a `shots` (salida de
    build_shot_features()): el acierto/volumen PREVIO de cada jugador en
    SU ZONA, calculados usando solo partidos ANTERIORES al del propio
    tiro -- nunca el propio partido ni partidos futuros. Mismo criterio
    "sin fuga de datos" que features_pregame.py aplica al modelo de
    resultado de partido (shift(1).expanding().mean()), aqui llevado al
    nivel de tiro/jugador.

    MOTIVACION (verificado en datos reales antes de escribir esto, sesion
    2026-09-21, no adivinado): agrupando los 2.45M tiros de las 11
    fuentes entrenables-y-calibradas por (source, player_id) dentro de la
    zona "aro" con >=50 intentos, la desviacion tipica REAL del FG% entre
    jugadores es ~4x mayor que la esperable solo por ruido binomial al
    mismo volumen (0.091 vs 0.022 con n medio=441); en "triple_arco" es
    ~2.3x (0.050 vs 0.022, n medio=478). zone/dist_m/angle_deg por si
    solos no explican esa variacion -- hay señal real de jugador
    (habilidad y/o rol) que el modelo anterior (solo geometria +
    shot_type + source) no podia aprovechar.

    COMO SE CALCULA (todo por PARTIDO, nunca por tiro suelto dentro de un
    partido -- un partido entero comparte el mismo prior, coherente con
    que esa informacion ya se conocia ANTES de que el partido empezara):
    1. Se agrega `shots` a nivel (source, player_id, zone, game_id):
       intentos y aciertos de ESE jugador en ESA zona en ESE partido.
    2. Se ordena por `games.date` (ya parseada por quien llama -- mismo
       parseo de fechas mixtas que chronological_split_by_game() en
       train_shot.py, format="mixed"/utc=True) y se acumula (cumsum)
       DESPLAZADO una posicion: el prior de un partido nunca incluye ese
       propio partido.
    3. Lo mismo a nivel (source, zone, game_id) pero sumando TODOS los
       jugadores de esa fuente -- "acierto de liga en esa zona hasta ese
       partido", tambien desplazado.
    4. El prior final del jugador se ENCOGE hacia el prior de liga (forma
       estandar beta-binomial/James-Stein) con peso `k_shrink` (intentos
       "virtuales" de liga que se le suman): con pocos intentos previos
       el prior queda cerca de la media de liga, con muchos se acerca al
       acierto realmente observado de ese jugador --
           player_prior_fg = (prior_makes + k_shrink * prior_league_fg)
                              / (prior_atts + k_shrink)
    5. `player_prior_n_log` = log1p(prior_atts) -- volumen previo del
       jugador en esa zona, en escala logaritmica: una feature separada
       de la habilidad (dos jugadores con el mismo FG% previo pero muy
       distinto volumen no son igual de fiables, y el volumen en si
       correlaciona con rol -- un pivot con mucho volumen de "aro" no es
       lo mismo que un exterior con 3 tiros de aro sueltos).

    Los PRIMEROS partidos de cada (source, zone) -- sin ningun partido
    anterior de esa fuente -- se quedan sin prior de liga real (0
    intentos previos); esos pocos casos usan como prior de liga el FG%
    global de esa (source, zone) sobre TODO `shots` (la unica fuga de
    datos real de esta funcion, limitada a un puñado de partidos por
    fuente al principio de su historico, nunca al grueso de filas).

    `games` debe tener las columnas (source, game_id, date), con `date`
    YA parseada a un tipo ordenable (mismo formato que produce
    chronological_split_by_game() en train_shot.py) -- esta funcion no
    repite ese parseo.
    """
    needed_shot_cols = {"source", "game_id", "player_id", "zone", "made"}
    missing = needed_shot_cols - set(shots.columns)
    if missing:
        raise ValueError(f"add_shot_priors(): faltan columnas en shots: {sorted(missing)}")
    needed_game_cols = {"source", "game_id", "date"}
    if not needed_game_cols <= set(games.columns):
        raise ValueError("add_shot_priors(): games debe tener columnas source, game_id, date")

    sd = shots.merge(games[["source", "game_id", "date"]], on=["source", "game_id"], how="left")
    n_sin_fecha = sd["date"].isna().sum()
    if n_sin_fecha:
        raise ValueError(
            f"add_shot_priors(): {n_sin_fecha} tiros sin fecha de partido tras el merge con "
            f"`games` -- revisar que `games` cubra todos los (source, game_id) de `shots`."
        )

    # 1) nivel jugador-zona-partido: intentos/aciertos de ESE jugador en
    # ESA zona en ESE partido, acumulados y desplazados por fecha.
    player_game = (
        sd.groupby(["source", "player_id", "zone", "game_id", "date"])
        .agg(makes=("made", "sum"), atts=("made", "size"))
        .reset_index()
        .sort_values(["source", "player_id", "zone", "date", "game_id"])
    )
    g = player_game.groupby(["source", "player_id", "zone"])
    player_game["prior_makes"] = g["makes"].cumsum() - player_game["makes"]
    player_game["prior_atts"] = g["atts"].cumsum() - player_game["atts"]

    # 2) nivel liga-zona-partido: mismo patron pero sumando TODOS los
    # jugadores de esa fuente, para tener el prior de liga hacia el que
    # encoger el prior del jugador.
    league_game = (
        sd.groupby(["source", "zone", "game_id", "date"])
        .agg(makes=("made", "sum"), atts=("made", "size"))
        .reset_index()
        .sort_values(["source", "zone", "date", "game_id"])
    )
    lg = league_game.groupby(["source", "zone"])
    league_game["prior_league_makes"] = lg["makes"].cumsum() - league_game["makes"]
    league_game["prior_league_atts"] = lg["atts"].cumsum() - league_game["atts"]

    fallback_fg = sd.groupby(["source", "zone"])["made"].mean().rename("fallback_fg")
    league_game = league_game.merge(fallback_fg, on=["source", "zone"], how="left")
    league_game["prior_league_fg"] = np.where(
        league_game["prior_league_atts"] > 0,
        league_game["prior_league_makes"] / league_game["prior_league_atts"].replace(0, np.nan),
        league_game["fallback_fg"],
    )

    # 3) une el prior de liga al de jugador y aplica el encogido.
    player_game = player_game.merge(
        league_game[["source", "zone", "game_id", "prior_league_fg"]],
        on=["source", "zone", "game_id"], how="left",
    )
    player_game["player_prior_fg"] = (
        player_game["prior_makes"] + k_shrink * player_game["prior_league_fg"]
    ) / (player_game["prior_atts"] + k_shrink)
    player_game["player_prior_n_log"] = np.log1p(player_game["prior_atts"])

    # 4) reparte el prior (uno por partido) a TODOS los tiros de ese
    # partido -- toda esa informacion ya se conocia antes de que
    # empezara, asi que todos sus tiros comparten el mismo valor.
    out = sd.merge(
        player_game[["source", "player_id", "zone", "game_id",
                      "player_prior_fg", "player_prior_n_log"]],
        on=["source", "player_id", "zone", "game_id"], how="left",
    )
    return out.drop(columns=["date"])
