"""Extractor de la NBA sobre la libreria nba_api.

Implementa la interfaz comun definida en src/scraper/base.py
(BasketballSource). Es el primer modulo de fuente del proyecto: se eligio
la NBA para empezar porque nba_api es, con diferencia, el ecosistema de
librerias Python mas maduro para baloncesto (ver plano del proyecto,
seccion 05) -- sirve para validar que la interfaz comun de base.py
funciona en la practica antes de enfrentarse a fuentes mas fragiles
(HTML propio, PostBacks de ASP.NET, tablas comentadas...).

NBA es una TRAINABLE_SOURCE (ver base.py): sus datos si pueden usarse en
train.py, sin la restriccion legal que si aplica a CBB/WNBA.

Estandar de codigo del proyecto (memoria de curso + regla del usuario):
cada llamada a nba_api que pueda fallar por red, cambios de esquema o el
bloqueo anti-bot de Akamai/Cloudflare identifica el error concreto y lo
relanza como NbaSourceError con contexto (que peticion, con que
parametros), en vez de dejar que una excepcion generica pare la
ejecucion en seco. Quien orquesta el scraping (p. ej. main.py iterando
partidos) puede capturar NbaSourceError y seguir con el siguiente
partido en vez de abortar todo el proceso.

AVISO IMPORTANTE DE DESARROLLO: BoxScoreTraditionalV2 -- el endpoint que
se documento inicialmente en el plano -- esta deprecado desde la
temporada 2025-26 (nba_api ya no recibe datos de el). Este modulo usa en
su lugar BoxScoreTraditionalV3, que cambia el naming de columnas a
camelCase (gameId, personId, fieldGoalsMade...). Firmas y nombres de
columnas de los otros tres endpoints (CommonTeamRoster, LeagueGameFinder,
ShotChartDetail) se verificaron contra el codigo fuente de la version
instalada de nba_api (1.11.4), no solo de memoria.

PROBADO EN VIVO (equipo del alumno, season=2025-26, fuera del entorno de
desarrollo -- ver mas abajo por que): los cinco metodos funcionan contra
la API real. Confirmado ahi mismo que "minutes" en BoxScoreTraditionalV3
llega como float ya en minutos decimales (p. ej. 34.483333...), no como
string "MM:SS" ni ISO 8601 -- _parse_minutes conserva sus otras dos
ramas (":" y "PT#M#S") por si algun otro endpoint futuro los usa, pero
en la practica para este metodo caen siempre en la rama float() directa.
list_games() descarta unos pocos game_id (tipicamente <1%) para los que
LeagueGameFinder no devuelve el par local/visitante completo (partidos
atipicos como el All-Star Weekend); se registran con logger.warning y se
omiten, no paran la ejecucion -- comportamiento intencionado, no un bug.

NOTA SOBRE CABECERAS: una primera version de este modulo definia su
propio dict de cabeceras HTTP minimo (User-Agent/Referer/Origin) y lo
pasaba a cada endpoint. Eso rompia las peticiones (RemoteDisconnected):
nba_api SUSTITUYE por completo su propio juego de cabeceras (mas
completo, afinado contra la proteccion Akamai) en cuanto se le pasa
cualquier `headers` no-None -- ver comentario junto a cada llamada mas
abajo. La solucion fue dejar `headers=None` y confiar en las cabeceras
por defecto de la libreria.

LIMITACION DEL ENTORNO DE DESARROLLO (no del codigo): el entorno donde
se escribio este modulo no tiene salida de red hacia stats.nba.com
(bloqueada por el proxy de la organizacion), asi que la implementacion
inicial y la correccion de cabeceras se hicieron sin poder probar contra
la API real -- de ahi el bug de cabeceras de arriba, que solo salio a
la luz al probarlo en un equipo con acceso de verdad. Cualquier cambio
futuro a este modulo deberia probarse igual, fuera de este entorno.
"""

from __future__ import annotations

import logging
import re
import time

from nba_api.stats.static import teams as nba_static_teams
from nba_api.stats.endpoints import (
    boxscoretraditionalv3,
    commonteamroster,
    leaguegamefinder,
    shotchartdetail,
)

from src.scraper.sports_reference import RateLimitedError
from src.scraper.base import (
    BoxscoreData,
    GameRef,
    Player,
    PlayerStatLine,
    Shot,
    Team,
    TeamStatLine,
)

logger = logging.getLogger(__name__)

# No se pasan cabeceras propias a nba_api (headers=None en cada endpoint,
# mas abajo). nba_api trae su propio juego de cabeceras por defecto
# (nba_api.stats.library.http.STATS_HEADERS: Host, Accept, Accept-Language,
# Accept-Encoding, Connection, Pragma, Cache-Control, Sec-Ch-Ua,
# Sec-Ch-Ua-Mobile, Sec-Fetch-Dest, ademas de User-Agent/Referer) afinado
# para pasar la proteccion Akamai de stats.nba.com. Un primer intento de
# este modulo definia un dict propio con solo 3 cabeceras (User-Agent,
# Referer, Origin) y lo pasaba explicitamente -- pero nba_api SUSTITUYE
# por completo sus cabeceras por defecto en cuanto se le pasa cualquier
# valor no-None (ver library/http.py: send_api_request, linea
# "request_headers = headers if headers is not None else self.headers"),
# asi que las peticiones salian sin Sec-Ch-Ua/Sec-Fetch-Dest/Accept-Encoding
# etc. y Akamai las cortaba en seco (RemoteDisconnected, confirmado en
# pruebas reales -- ver decisiones_proyecto.md). Dejar headers=None deja
# que nba_api use su propio juego, mas completo y mantenido por el
# proyecto contra los cambios de Akamai.

_DEFAULT_TIMEOUT = 30  # segundos, igual que el valor por defecto de nba_api

# Peticiones POR PARTIDO (boxscore y shot chart). stats.nba.com limita el
# ritmo de las conexiones desatendidas: tras ~1000 peticiones seguidas
# empiezan bloques enteros de timeouts (visto en la Pi: 111 de 1230 partidos
# de 2018-19, ~55 min perdidos esperando 30 s por cada uno). Estrategia:
#  - timeout mas corto por partido (un boxscore sano responde en segundos);
#  - si el servidor iba bien, se reintenta con espera creciente;
#  - si ya viene una racha de fallos NO se reintenta (cada intento cuesta un
#    timeout entero) y, al llegar a _FAIL_STREAK_LIMIT seguidos, se hace una
#    pausa larga para dejar que se levante el limite antes de seguir.
_GAME_TIMEOUT = 20
_MAX_RETRIES = 2
_RETRY_BACKOFF_BASE = 5.0
_RETRY_MAX_DELAY = 60.0
_FAIL_STREAK_LIMIT = 5
_COOLDOWN_SECONDS = 120.0
# Si tras _MAX_COOLDOWNS pausas largas seguidas no ha funcionado NI UNA peticion,
# no es un pico de trafico sino un bloqueo duradero: se para la ingesta con
# RateLimitedError (el pipeline guarda lo ya descargado y anota el fallo) en
# vez de seguir horas con timeouts. Se reanuda mas tarde y la cache salta lo hecho.
_MAX_COOLDOWNS = 2
_fail_streak = 0              # fallos definitivos seguidos (estado del modulo)
_cooldowns_without_success = 0


def _request_with_retry(make_request, what: str):
    """Ejecuta `make_request()` con reintentos/enfriamiento (ver arriba).
    Relanza la ultima excepcion si se agotan los intentos."""
    global _fail_streak, _cooldowns_without_success
    if _fail_streak >= _FAIL_STREAK_LIMIT:
        if _cooldowns_without_success >= _MAX_COOLDOWNS:
            _fail_streak = 0
            _cooldowns_without_success = 0
            raise RateLimitedError(
                f"stats.nba.com no responde tras {_MAX_COOLDOWNS} pausas largas "
                f"seguidas ({what}): posible bloqueo temporal de la IP. Se para "
                f"la ingesta; lo descargado queda guardado. Reintenta en unas horas."
            )
        _cooldowns_without_success += 1
        logger.warning(
            "%d fallos seguidos pidiendo a stats.nba.com (posible limite de "
            "ritmo): pausa de %.0f s antes de seguir.", _fail_streak, _COOLDOWN_SECONDS,
        )
        time.sleep(_COOLDOWN_SECONDS)
        _fail_streak = 0
    retries = _MAX_RETRIES if _fail_streak == 0 else 0
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = make_request()
            _fail_streak = 0
            _cooldowns_without_success = 0
            return result
        except Exception as exc:  # nba_api lanza requests.* / JSONDecodeError / ...
            last_exc = exc
            if attempt < retries:
                delay = min(_RETRY_BACKOFF_BASE * (2 ** attempt), _RETRY_MAX_DELAY)
                logger.info("%s: intento %d fallo (%s); reintento en %.0f s.",
                            what, attempt + 1, exc, delay)
                time.sleep(delay)
    _fail_streak += 1
    raise last_exc  # type: ignore[misc]

_MINUTES_ISO_RE = re.compile(r"PT(\d+)M(\d+(?:\.\d+)?)S")


class NbaSourceError(RuntimeError):
    """Fallo al extraer datos de nba_api, con contexto de que peticion
    concreta ha fallado y por que -- pensado para que el codigo que
    orquesta el scraping pueda capturarlo especificamente y decidir si
    reintenta, salta el partido/equipo o para del todo."""


def _parse_minutes(raw: object) -> float:
    """Convierte el campo de minutos jugados a float. Confirmado en vivo
    (ver aviso al inicio del modulo): BoxScoreTraditionalV3 devuelve
    "minutes" como float ya en minutos decimales, asi que en la practica
    siempre cae en la rama float() de abajo. Se conservan ademas la rama
    "MM:SS" (formato clasico de BoxScoreTraditionalV2/LeagueGameFinder) y
    la rama ISO 8601 "PT#M#S" por si algun otro endpoint las usara. Nunca
    lanza: si el formato no se reconoce, registra un aviso y devuelve 0.0
    en vez de tirar abajo el resto del boxscore por un campo de minutos.
    """
    if raw is None:
        return 0.0

    text = str(raw).strip()
    if not text or text in {"None", "0", "0.0", "0:00"}:
        return 0.0

    iso_match = _MINUTES_ISO_RE.fullmatch(text)
    if iso_match:
        minutes, seconds = iso_match.groups()
        return int(minutes) + float(seconds) / 60.0

    if ":" in text:
        try:
            minutes_str, seconds_str = text.split(":", 1)
            return int(minutes_str) + int(seconds_str) / 60.0
        except ValueError as exc:
            logger.warning("No se pudo parsear el campo de minutos %r: %s", raw, exc)
            return 0.0

    try:
        return float(text)
    except ValueError:
        logger.warning("Formato de minutos no reconocido: %r; se registra como 0.0", raw)
        return 0.0


def _is_missing(value: object) -> bool:
    """True si `value` es None o NaN (pandas representa los huecos como
    NaN incluso en columnas nominalmente numericas)."""
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN != NaN es la unica forma fiable sin depender de numpy aqui
    except Exception:
        return False


def _team_val(row) -> int:
    """Aproximacion al PIR/Valoracion de Euroleague (formula en el plano,
    seccion 07) con los campos que de verdad ofrece el boxscore de la
    NBA. OJO: no es el PIR oficial -- a la NBA le faltan "fouls drawn" y
    "blocks against" en este endpoint, asi que esta cifra sirve como
    proxy interno para mantener la forma del esquema comun, no como dato
    validable contra una fuente oficial (a diferencia del val real de
    Euroleague, que si se puede contrastar -- ver seccion 07 del plano).
    """
    made = int(row["fieldGoalsMade"]) + int(row["freeThrowsMade"])
    attempted = int(row["fieldGoalsAttempted"]) + int(row["freeThrowsAttempted"])
    missed = attempted - made
    return (
        int(row["points"])
        + int(row["reboundsTotal"])
        + int(row["assists"])
        + int(row["steals"])
        + int(row["blocks"])
        - missed
        - int(row["turnovers"])
        - int(row["foulsPersonal"])
    )


class NbaSource:
    """Extractor de la NBA. Implementa BasketballSource (ver base.py)."""

    source = "nba"

    def __init__(self, season: str = "2025-26") -> None:
        """`season` en el formato de nba_api: "2025-26".

        list_players() usa `self.season` para resolver el roster de un
        equipo en un momento concreto -- a diferencia de list_teams() y
        list_games(), que reciben su propia `season` en cada llamada
        (siguiendo la firma de BasketballSource), get_boxscore() y
        get_shots() no la necesitan porque el game_id de la NBA ya
        codifica la temporada.
        """
        self.season = season

    # -- equipos / jugadores -------------------------------------------

    def list_teams(self, season: str) -> list[Team]:
        """Equipos de la NBA. La lista de franquicias apenas cambia de
        una temporada a otra, asi que se resuelve con el modulo estatico
        de nba_api (sin llamada de red) en vez de pedir un endpoint de
        estadisticas por temporada -- mas rapido y no depende de Akamai
        para algo que casi nunca varia. `season` se acepta por firma de
        BasketballSource pero no filtra el resultado.
        """
        try:
            raw_teams = nba_static_teams.get_teams()
        except Exception as exc:
            raise NbaSourceError(
                f"No se pudo obtener el listado estatico de equipos NBA "
                f"(season={season!r}): {exc}"
            ) from exc

        return [
            Team(team_id=str(t["id"]), name=t["full_name"], source=self.source)
            for t in raw_teams
        ]

    def list_players(self, team_id: str) -> list[Player]:
        """Roster de un equipo en `self.season` (ver __init__)."""
        try:
            roster = commonteamroster.CommonTeamRoster(
                team_id=team_id,
                season=self.season,
                headers=None,
                timeout=_DEFAULT_TIMEOUT,
            )
            df = roster.common_team_roster.get_data_frame()
        except Exception as exc:
            raise NbaSourceError(
                f"Fallo pidiendo el roster de team_id={team_id!r}, "
                f"season={self.season!r}: {exc}"
            ) from exc

        try:
            return [
                Player(
                    player_id=str(row["PLAYER_ID"]),
                    name=str(row["PLAYER"]),
                    team_id=str(team_id),
                    source=self.source,
                )
                for _, row in df.iterrows()
            ]
        except KeyError as exc:
            raise NbaSourceError(
                f"CommonTeamRoster devolvio columnas inesperadas para "
                f"team_id={team_id!r}: {exc}"
            ) from exc

    # -- partidos --------------------------------------------------------

    def list_games(self, season: str) -> list[GameRef]:
        """Todos los partidos de temporada regular de `season` (formato
        "2025-26"). LeagueGameFinder devuelve dos filas por partido (una
        por equipo); se agrupan por GAME_ID para construir un GameRef
        por partido con local/visitante ya resueltos.
        """
        try:
            finder = leaguegamefinder.LeagueGameFinder(
                season_nullable=season,
                season_type_nullable="Regular Season",
                league_id_nullable="00",
                headers=None,
                timeout=_DEFAULT_TIMEOUT,
            )
            df = finder.league_game_finder_results.get_data_frame()
        except Exception as exc:
            raise NbaSourceError(
                f"Fallo pidiendo el calendario de la NBA para "
                f"season={season!r}: {exc}"
            ) from exc

        games: dict[str, dict] = {}
        try:
            for _, row in df.iterrows():
                game_id = str(row["GAME_ID"])
                is_home = "@" not in str(row["MATCHUP"])
                entry = games.setdefault(
                    game_id, {"date": str(row["GAME_DATE"])}
                )
                score = None if _is_missing(row["PTS"]) else int(row["PTS"])
                if is_home:
                    entry["home_team_id"] = str(row["TEAM_ID"])
                    entry["home_score"] = score
                else:
                    entry["away_team_id"] = str(row["TEAM_ID"])
                    entry["away_score"] = score
        except (KeyError, ValueError) as exc:
            raise NbaSourceError(
                f"LeagueGameFinder devolvio columnas o valores inesperados "
                f"para season={season!r}: {exc}"
            ) from exc

        result: list[GameRef] = []
        for game_id, data in games.items():
            if "home_team_id" not in data or "away_team_id" not in data:
                logger.warning(
                    "Partido %s de season=%s con datos incompletos "
                    "(falta local o visitante); se omite.",
                    game_id, season,
                )
                continue
            result.append(
                GameRef(
                    game_id=game_id,
                    source=self.source,
                    season=season,
                    round=None,  # la NBA no numera "jornada" como las ligas europeas
                    date=data["date"],
                    home_team_id=data["home_team_id"],
                    away_team_id=data["away_team_id"],
                    home_score=data.get("home_score"),
                    away_score=data.get("away_score"),
                    played=data.get("home_score") is not None,
                )
            )
        return result

    # -- boxscore / tiros --------------------------------------------------

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        """Usa BoxScoreTraditionalV3 (V2 esta deprecado desde 2025-26,
        ver aviso al inicio del modulo)."""
        try:
            box = _request_with_retry(
                lambda: boxscoretraditionalv3.BoxScoreTraditionalV3(
                    game_id=game_id,
                    headers=None,
                    timeout=_GAME_TIMEOUT,
                ),
                f"boxscore {game_id}",
            )
            team_df = box.team_stats.get_data_frame()
            player_df = box.player_stats.get_data_frame()
        except Exception as exc:
            raise NbaSourceError(
                f"Fallo pidiendo el boxscore del partido "
                f"game_id={game_id!r}: {exc}"
            ) from exc

        try:
            team_stats = [
                TeamStatLine(
                    game_id=game_id,
                    team_id=str(row["teamId"]),
                    t2_made=int(row["fieldGoalsMade"]) - int(row["threePointersMade"]),
                    t2_att=int(row["fieldGoalsAttempted"]) - int(row["threePointersAttempted"]),
                    t3_made=int(row["threePointersMade"]),
                    t3_att=int(row["threePointersAttempted"]),
                    ft_made=int(row["freeThrowsMade"]),
                    ft_att=int(row["freeThrowsAttempted"]),
                    reb_off=int(row["reboundsOffensive"]),
                    reb_def=int(row["reboundsDefensive"]),
                    ast=int(row["assists"]),
                    stl=int(row["steals"]),
                    turnovers=int(row["turnovers"]),
                    blocks=int(row["blocks"]),
                    fouls=int(row["foulsPersonal"]),
                    val=_team_val(row),
                )
                for _, row in team_df.iterrows()
            ]
            player_stats = [
                PlayerStatLine(
                    game_id=game_id,
                    player_id=str(row["personId"]),
                    player_name=f"{row['firstName']} {row['familyName']}".strip(),
                    team_id=str(row["teamId"]),
                    minutes=_parse_minutes(row["minutes"]),
                    points=0 if _is_missing(row["points"]) else int(row["points"]),
                    rebounds=0 if _is_missing(row["reboundsTotal"]) else int(row["reboundsTotal"]),
                    assists=0 if _is_missing(row["assists"]) else int(row["assists"]),
                    val=_team_val(row),
                    plus_minus=(
                        None if _is_missing(row.get("plusMinusPoints"))
                        else int(row["plusMinusPoints"])
                    ),
                )
                for _, row in player_df.iterrows()
                if not _is_missing(row["minutes"]) and str(row["minutes"]).strip() not in {"", "0", "None"}
            ]
        except KeyError as exc:
            raise NbaSourceError(
                f"BoxScoreTraditionalV3 devolvio columnas inesperadas para "
                f"game_id={game_id!r}: {exc}"
            ) from exc

        return BoxscoreData(game_id=game_id, team_stats=team_stats, player_stats=player_stats)

    def get_shots(self, game_id: str) -> list[Shot]:
        """Coordenadas de tiro del partido, en decimas de pie (unidad
        nativa de nba_api) -- la normalizacion a metros FIBA para poder
        comparar entre fuentes es responsabilidad de src/pipeline/, no
        de este modulo (ver plano, seccion 04). team_id=0/player_id=0
        piden todos los tiros del partido sin filtrar por equipo o
        jugador.
        """
        try:
            chart = _request_with_retry(
                lambda: shotchartdetail.ShotChartDetail(
                    team_id=0,
                    player_id=0,
                    game_id_nullable=game_id,
                    context_measure_simple="FGA",
                    season_nullable=self.season,
                    headers=None,
                    timeout=_GAME_TIMEOUT,
                ),
                f"shot chart {game_id}",
            )
            df = chart.shot_chart_detail.get_data_frame()
        except Exception as exc:
            raise NbaSourceError(
                f"Fallo pidiendo el shot chart del partido "
                f"game_id={game_id!r}: {exc}"
            ) from exc

        try:
            return [
                Shot(
                    game_id=game_id,
                    player_id=str(row["PLAYER_ID"]),
                    team_id=str(row["TEAM_ID"]),
                    loc_x=float(row["LOC_X"]),
                    loc_y=float(row["LOC_Y"]),
                    made=bool(int(row["SHOT_MADE_FLAG"])),
                    shot_type="3" if "3PT" in str(row["SHOT_TYPE"]) else "2",
                )
                for _, row in df.iterrows()
            ]
        except KeyError as exc:
            raise NbaSourceError(
                f"ShotChartDetail devolvio columnas inesperadas para "
                f"game_id={game_id!r}: {exc}"
            ) from exc
