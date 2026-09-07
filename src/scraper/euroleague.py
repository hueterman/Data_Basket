"""Extractor de Euroleague Basketball (Euroliga / BKT EuroCup / NextGen).

Implementa la interfaz comun definida en src/scraper/base.py
(BasketballSource). Segundo modulo de fuente del proyecto, tras nba.py.

A diferencia de la NBA, aqui no existe una libreria Python madura y
mantenida que envuelva la API (euroleague-api en PyPI esta en version
0.1.1 y no hay garantia de que cubra las tres competiciones ni de que
siga el mismo formato que aqui se documenta) -- este modulo llama
directamente, con requests, a los endpoints REST documentados en el
plano del proyecto (seccion 05).

VERIFICADO CONTRA RESPUESTAS REALES (vía fetch, no solo documentacion):
a diferencia del primer borrador de nba.py -- que se escribio sin poder
probar contra la API real y tuvo que corregirse despues --, los tres
endpoints que usa este modulo (calendario v2, Boxscore, Points) se
inspeccionaron contra respuestas JSON reales antes de escribir el
codigo de mapeo de campos. Esto saco a la luz varias correcciones sobre
lo que decia el plano hasta ahora:

- El calendario v2 (`/v2/competitions/{code}/seasons/{season}/games`)
  devuelve el array bajo una clave "data" (`{"data": [...]}`), no un
  array directo como asumia el plano.
- Los equipos NO se llaman "home"/"away" en la respuesta real, sino
  "local"/"road" (terminologia de FIBA LiveStats) -- este modulo
  mapea local->home_*, road->away_* para encajar con el nombre generico
  de la interfaz comun (base.py).
- El calendario v2 SI incluye el resultado (local.score / road.score) y
  los parciales por cuarto, algo que el plano daba por no disponible
  (asumia que hacia falta una llamada aparte a Header). No hace falta
  aqui: list_games() ya devuelve home_score/away_score sin llamadas
  adicionales.
- Se localizo (sin estar documentado en el plano) un endpoint de
  plantilla/roster real y funcional:
  `/v2/competitions/{code}/seasons/{season}/clubs/{clubCode}/people`
  -- devuelve jugadores Y cuerpo tecnico mezclados; se filtra por
  type == "J" (jugador) para quedarnos solo con la plantilla de
  jugadores.

AVISO IMPORTANTE -- IDs de jugador NO coinciden entre endpoints: el
roster (`clubs/.../people`) identifica a cada persona con
`person.code` (p. ej. "004132"), mientras que Boxscore identifica al
mismo jugador con un `Player_ID` de formato distinto (p. ej.
"P012099"). No hay forma directa de cruzar list_players() con
get_boxscore() por ID; cualquier pipeline que necesite relacionar
ambos tendra que hacerlo por nombre (Player/person.name), con el
riesgo de desajuste que eso conlleva (acentos, orden nombre/apellido).
Documentado aqui para que quien construya src/pipeline/ lo tenga en
cuenta desde el principio, no lo descubra con un join vacio.

NextGen (ANGT): TRAINABLE_SOURCES incluye "nextgen" (ver base.py) y
este modulo acepta competition="nextgen", pero list_teams()/list_games()
lanzan EuroleagueSourceError para esa competicion -- el calendario v2
solo documenta (y se ha verificado en vivo) competitionCode "E" y "U";
no hay constancia de que "J"/"JT" funcione ahi, y NextGen no usa liga
regular sino torneos + Final Four, lo que probablemente exige un
endpoint distinto. get_boxscore()/get_shots() SI deberian funcionar
para NextGen si se les da un game_id valido obtenido por otra via
(los endpoints Boxscore/Points de live.euroleague.net estan
documentados como validos para las tres competiciones) -- sin probar
en vivo.

PROBADO EN VIVO por el usuario en su propio equipo (temporadas E2024 y
U2024, ya cerradas): los cinco metodos funcionan contra la API real,
para Euroliga Y para EuroCup, sin ningun ajuste tras la primera
version -- a diferencia de nba.py, que necesito una correccion de
cabeceras despues de la primera prueba en vivo. Esto tambien confirma
en vivo, y ya no es una duda, que el endpoint de plantilla
(`clubs/{code}/people`) funciona igual para EuroCup ("U") que para
Euroliga ("E") -- se probo explicitamente el roster de un club de
EuroCup (Dreamland Gran Canaria) y devolvio 18 jugadores tipo "J"
correctamente. NextGen sigue sin probarse (list_teams()/list_games()
lo rechazan explicitamente, ver aviso arriba).
"""

from __future__ import annotations

import logging
import re

import requests

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

_V2_BASE = "https://api-live.euroleague.net/v2"
_LIVE_BASE = "https://live.euroleague.net/api"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.euroleaguebasketball.net/",
}

_DEFAULT_TIMEOUT = 30

# competitionCode de la API v2, solo para las competiciones donde el
# calendario v2 esta confirmado en vivo (ver aviso de NextGen arriba).
_COMPETITION_CODES = {
    "euroleague": "E",
    "eurocup": "U",
}

# Prefijo de seasoncode esperado por competicion (ver plano, seccion 05
# -- tabla "Prefijo de seasoncode por competicion, resuelto"). Se usa
# solo como aviso temprano si alguien pasa un season con el prefijo
# equivocado; NextGen tiene mas de un prefijo valido (J/JT/JTM/JTV) asi
# que no se valida de forma estricta.
_SEASON_PREFIXES = {
    "euroleague": ("E",),
    "eurocup": ("U",),
    "nextgen": ("J", "JT", "JTM", "JTV"),
}

_SHOT_ACTIONS = {"2FGA", "2FGM", "3FGA", "3FGM"}

# "DNP" (did not play) es literal en el campo Minutes de Boxscore; el
# resto llega como "MM:SS" (incluida la fila de totales, p. ej. "200:00").
_MINUTES_RE = re.compile(r"^(\d+):(\d{2})$")


class EuroleagueSourceError(RuntimeError):
    """Fallo al extraer datos de la API de Euroleague Basketball, con
    contexto de que peticion concreta ha fallado y por que -- mismo
    estandar de codigo del proyecto que NbaSourceError (ver nba.py):
    identificar el error concreto y relanzarlo con contexto, en vez de
    dejar que una excepcion generica pare la ejecucion en seco."""


def _parse_minutes(raw: object) -> float:
    """"MM:SS" -> minutos en float. "DNP"/None/vacio -> 0.0. Nunca
    lanza: si el formato no se reconoce, registra un aviso y devuelve
    0.0 en vez de tirar abajo el resto del boxscore."""
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if not text or text == "DNP":
        return 0.0
    match = _MINUTES_RE.match(text)
    if match:
        minutes, seconds = match.groups()
        return int(minutes) + int(seconds) / 60.0
    logger.warning("Formato de minutos no reconocido: %r; se registra como 0.0", raw)
    return 0.0


class EuroleagueSource:
    """Un objeto = una competicion (euroleague/eurocup/nextgen). Para
    trabajar con varias, se crean varias instancias -- igual que
    TRAINABLE_SOURCES las trata como fuentes independientes en base.py.
    """

    def __init__(self, competition: str, season: str | None = None):
        if competition not in _SEASON_PREFIXES:
            raise ValueError(
                f"Competicion desconocida: {competition!r}. Validas: "
                f"{sorted(_SEASON_PREFIXES)}"
            )
        self.source = competition
        self.season = season  # usado por list_players(), que no recibe season
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # -- infraestructura HTTP ---------------------------------------------

    def _get_json(self, url: str, params: dict, context: str) -> dict:
        try:
            response = self._session.get(url, params=params, timeout=_DEFAULT_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise EuroleagueSourceError(f"Fallo pidiendo {context}: {exc}") from exc

    def _check_season_prefix(self, season: str) -> None:
        prefixes = _SEASON_PREFIXES[self.source]
        if not season.startswith(prefixes):
            raise ValueError(
                f"season={season!r} no empieza por un prefijo valido de "
                f"{self.source!r} ({prefixes!r}); revisa la tabla de "
                f"seasoncode del plano (seccion 05) antes de continuar."
            )

    def _list_games_raw(self, season: str) -> list[dict]:
        if self.source not in _COMPETITION_CODES:
            raise EuroleagueSourceError(
                f"El calendario v2 no esta confirmado en vivo para "
                f"source={self.source!r} (ver docstring del modulo, "
                f"aviso sobre NextGen); no se puede listar partidos ni "
                f"equipos para esta competicion todavia."
            )
        self._check_season_prefix(season)
        competition_code = _COMPETITION_CODES[self.source]
        payload = self._get_json(
            f"{_V2_BASE}/competitions/{competition_code}/seasons/{season}/games",
            params={},
            context=f"calendario de {self.source}, season={season!r}",
        )
        games = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(games, list):
            raise EuroleagueSourceError(
                f"Respuesta de calendario con forma inesperada para "
                f"{self.source}, season={season!r}: "
                f"{type(payload).__name__}"
            )
        return games

    # -- equipos / jugadores ------------------------------------------------

    def list_teams(self, season: str) -> list[Team]:
        """Deriva el listado de equipos a partir del calendario de la
        temporada (no hay endpoint de "listado de equipos" documentado
        ni localizado por separado): un equipo por cada club_code unico
        visto en local/road."""
        games = self._list_games_raw(season)
        teams: dict[str, str] = {}
        try:
            for game in games:
                for side in ("local", "road"):
                    club = game.get(side, {}).get("club", {})
                    code = club.get("code")
                    if code:
                        teams[code] = club.get("name", code)
        except AttributeError as exc:
            raise EuroleagueSourceError(
                f"Forma inesperada en el calendario de {self.source}, "
                f"season={season!r} al extraer equipos: {exc}"
            ) from exc

        return [
            Team(team_id=code, name=name, source=self.source)
            for code, name in teams.items()
        ]

    def list_players(self, team_id: str) -> list[Player]:
        """Plantilla de `team_id` en `self.season` (constructor). Usa un
        endpoint localizado pero NO documentado en el plano original
        (`clubs/{team_id}/people`) -- devuelve jugadores y cuerpo
        tecnico mezclados, filtrado aqui por type == "J". Lanza si no
        se fijo `season` al construir la instancia: list_players() no
        recibe season por la interfaz comun (ver base.py)."""
        if not self.season:
            raise EuroleagueSourceError(
                "list_players() necesita self.season -- crea "
                "EuroleagueSource(competition=..., season=...) con la "
                "temporada fijada."
            )
        if self.source not in _COMPETITION_CODES:
            raise EuroleagueSourceError(
                f"Roster no confirmado en vivo para source={self.source!r} "
                f"(ver aviso de NextGen en el docstring del modulo)."
            )
        competition_code = _COMPETITION_CODES[self.source]
        payload = self._get_json(
            f"{_V2_BASE}/competitions/{competition_code}/seasons/"
            f"{self.season}/clubs/{team_id}/people",
            params={},
            context=f"plantilla de {self.source}, team_id={team_id!r}, season={self.season!r}",
        )
        entries = payload if isinstance(payload, list) else payload.get("data")
        if not isinstance(entries, list):
            raise EuroleagueSourceError(
                f"Respuesta de plantilla con forma inesperada para "
                f"team_id={team_id!r}: {type(payload).__name__}"
            )

        try:
            return [
                Player(
                    player_id=str(entry["person"]["code"]),
                    name=str(entry["person"]["name"]),
                    team_id=str(team_id),
                    source=self.source,
                )
                for entry in entries
                if entry.get("type") == "J"
            ]
        except KeyError as exc:
            raise EuroleagueSourceError(
                f"La plantilla de team_id={team_id!r} devolvio entradas "
                f"con forma inesperada: {exc}"
            ) from exc

    # -- partidos -----------------------------------------------------------

    def list_games(self, season: str) -> list[GameRef]:
        """Todos los partidos de `season` para esta competicion. A
        diferencia de nba.py (LeagueGameFinder da dos filas por
        partido), el calendario v2 ya da un registro completo por
        partido -- no hace falta agrupar."""
        games_raw = self._list_games_raw(season)
        result: list[GameRef] = []
        for game in games_raw:
            try:
                local = game["local"]
                road = game["road"]
                played = bool(game.get("played"))
                result.append(
                    GameRef(
                        game_id=str(game["identifier"]),
                        source=self.source,
                        season=season,
                        round=game.get("round"),
                        date=game.get("utcDate") or game.get("date"),
                        home_team_id=str(local["club"]["code"]),
                        away_team_id=str(road["club"]["code"]),
                        home_score=local.get("score") if played else None,
                        away_score=road.get("score") if played else None,
                        played=played,
                    )
                )
            except KeyError as exc:
                logger.warning(
                    "Partido con forma inesperada en %s season=%s "
                    "(identifier=%s): falta %s; se omite.",
                    self.source, season, game.get("identifier", "?"), exc,
                )
                continue
        return result

    # -- boxscore / tiros -----------------------------------------------------

    def _split_game_id(self, game_id: str) -> tuple[str, str]:
        """game_id = identifier del calendario v2, formato
        "{seasoncode}_{gamecode}" (p. ej. "E2024_330") -- confirmado en
        vivo, no inventado (ver docstring del modulo)."""
        if "_" not in game_id:
            raise EuroleagueSourceError(
                f"game_id={game_id!r} no tiene el formato esperado "
                f"'SEASONCODE_GAMECODE' (p. ej. 'E2024_330'); "
                f"list_games() es quien debe producir estos IDs."
            )
        season_code, game_code = game_id.split("_", 1)
        return season_code, game_code

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        season_code, game_code = self._split_game_id(game_id)
        try:
            data = self._get_json(
                f"{_LIVE_BASE}/Boxscore",
                params={"gamecode": game_code, "seasoncode": season_code},
                context=f"boxscore de game_id={game_id!r}",
            )
            stats_by_team = data["Stats"]
        except KeyError as exc:
            raise EuroleagueSourceError(
                f"Boxscore devolvio forma inesperada para game_id={game_id!r}: {exc}"
            ) from exc

        team_stats: list[TeamStatLine] = []
        player_stats: list[PlayerStatLine] = []
        try:
            for team_block in stats_by_team:
                players = team_block.get("PlayersStats", [])
                team_code = (
                    str(players[0]["Team"]).strip() if players else None
                )

                totals = team_block.get("totr")
                if totals and team_code:
                    team_stats.append(
                        TeamStatLine(
                            game_id=game_id,
                            team_id=team_code,
                            t2_made=int(totals["FieldGoalsMade2"]),
                            t2_att=int(totals["FieldGoalsAttempted2"]),
                            t3_made=int(totals["FieldGoalsMade3"]),
                            t3_att=int(totals["FieldGoalsAttempted3"]),
                            ft_made=int(totals["FreeThrowsMade"]),
                            ft_att=int(totals["FreeThrowsAttempted"]),
                            reb_off=int(totals["OffensiveRebounds"]),
                            reb_def=int(totals["DefensiveRebounds"]),
                            ast=int(totals["Assistances"]),
                            stl=int(totals["Steals"]),
                            turnovers=int(totals["Turnovers"]),
                            blocks=int(totals["BlocksFavour"]),
                            fouls=int(totals["FoulsCommited"]),
                            val=int(totals["Valuation"]),
                        )
                    )
                elif not team_code:
                    logger.warning(
                        "game_id=%s: bloque de equipo sin PlayersStats, "
                        "se omite su fila de totales.", game_id,
                    )

                for row in players:
                    if str(row.get("Minutes")).strip() in {"DNP", "None", ""}:
                        continue
                    player_stats.append(
                        PlayerStatLine(
                            game_id=game_id,
                            player_id=str(row["Player_ID"]).strip(),
                            player_name=str(row["Player"]),
                            team_id=str(row["Team"]).strip(),
                            minutes=_parse_minutes(row["Minutes"]),
                            points=int(row["Points"]),
                            rebounds=int(row["TotalRebounds"]),
                            assists=int(row["Assistances"]),
                            val=int(row["Valuation"]),
                            plus_minus=(
                                None if row.get("Plusminus") is None
                                else int(row["Plusminus"])
                            ),
                        )
                    )
        except (KeyError, TypeError, ValueError) as exc:
            raise EuroleagueSourceError(
                f"Boxscore devolvio columnas o valores inesperados para "
                f"game_id={game_id!r}: {exc}"
            ) from exc

        return BoxscoreData(game_id=game_id, team_stats=team_stats, player_stats=player_stats)

    def get_shots(self, game_id: str) -> list[Shot]:
        """Coordenadas de tiro del partido, en la unidad nativa de la
        API (enteros centrados aprox. en el centro de la pista -- no es
        ni el sistema en decimas de pie de nba_api ni el de porcentaje
        de acb.com). La normalizacion a metros FIBA para comparar entre
        fuentes es responsabilidad de src/pipeline/, no de este modulo
        (ver plano, seccion 04). Solo se incluyen intentos de campo
        (2/3 puntos); tiros libres u otros tipos de accion que pudiera
        devolver el endpoint se descartan porque no llevan coordenadas
        con sentido de "tiro"."""
        season_code, game_code = self._split_game_id(game_id)
        try:
            data = self._get_json(
                f"{_LIVE_BASE}/Points",
                params={"gamecode": game_code, "seasoncode": season_code},
                context=f"tiros de game_id={game_id!r}",
            )
            rows = data["Rows"]
        except KeyError as exc:
            raise EuroleagueSourceError(
                f"Points devolvio forma inesperada para game_id={game_id!r}: {exc}"
            ) from exc

        try:
            return [
                Shot(
                    game_id=game_id,
                    player_id=str(row["ID_PLAYER"]).strip(),
                    team_id=str(row["TEAM"]).strip(),
                    loc_x=float(row["COORD_X"]),
                    loc_y=float(row["COORD_Y"]),
                    made=str(row["ID_ACTION"]).endswith("FGM"),
                    shot_type="3" if str(row["ID_ACTION"]).startswith("3") else "2",
                )
                for row in rows
                if row.get("ID_ACTION") in _SHOT_ACTIONS
            ]
        except (KeyError, ValueError) as exc:
            raise EuroleagueSourceError(
                f"Points devolvio columnas o valores inesperados para "
                f"game_id={game_id!r}: {exc}"
            ) from exc
