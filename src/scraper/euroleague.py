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

NEXTGEN (ANGT) RESUELTO EN VIVO (2026-09-19) -- ya no lanza
EuroleagueSourceError. Se investigo primero un documento externo
subido por el usuario que proponia un rediseño de la interfaz (season
simbolico "J2023"/"J2024" + parametro event/phase nuevo) -- verificado
contra la API real, ESE PLANTEAMIENTO ERA INNECESARIO. Lo confirmado
en vivo, con la misma cabecera Accept: application/json que usa este
modulo (para descartar que fuese solo cosa del navegador):

- competitionCode = "J" SI funciona en el calendario v2, exactamente
  igual que "E"/"U" -- misma forma de respuesta JSON ({"data": [...]},
  local/road/club.code/score, identifier="SEASONCODE_GAMECODE"),
  mismo endpoint de plantilla (clubs/{code}/people, type=="J" filtra
  jugadores igual), mismos Boxscore/Points de live.euroleague.net.
  Cero cambios de mapeo de campos hicieron falta -- todo el codigo de
  abajo (list_teams/list_games/list_players/get_boxscore/get_shots) es
  el MISMO para las tres competiciones, solo cambia el diccionario
  _COMPETITION_CODES.
- LO QUE SI ES DISTINTO (y el documento subido acertaba en la idea de
  fondo, aunque no en el formato del seasonCode): NextGen NO tiene un
  "season" por año como E/U. Cada edicion anual son ~5 seasonCode
  independientes, uno por sede clasificatoria mas uno para la Final
  Eight (ej. temporada 2023-24 real, verificada via
  /v2/competitions/J/seasons: JTC23 Podgorica, JTB23 Belgrado, JTD23
  Dubai, JTP23 Paris, JTF423 Berlin Finals -- cada uno con su propio
  `year`/fechas/ganador). No hay un codigo simbolico "J2023" que
  funcione solo -- hay que pasar el codigo de sede real
  (list_games(season="JTB23"), no season="J2023").
  Para saber que codigos de sede existen para un año dado: GET
  /v2/competitions/J/seasons (JSON {"data":[...], "total":N}) y
  filtrar por el campo "year" de cada entrada -- no hay otra forma de
  listarlos, no se puede adivinar el codigo de sede a partir del año.
- get_shots() tambien confirmado en vivo para NextGen (partido real
  JTB23_16, 186 filas en Points) -- y la calibracion de coordenadas es
  la MISMA que euroleague/eurocup (ver src/features.py,
  SHOT_COORD_CALIBRATION): las coordenadas observadas encajan con el
  radio de 6.75m del arco de 3 FIBA multiplicado por 100, igual que en
  Euroliga/EuroCup (mismo proveedor de datos por debajo).
- Decision de diseño (instruccion del usuario, 2026-09-19): NextGen se
  usa con codigo de sede directo, NO con un año simbolico que agregue
  varias sedes automaticamente -- mas simple de implementar y evita
  adivinar que sedes "cuentan" como la temporada de un año (algunas
  ediciones han tenido mas o menos de 4 clasificatorios historicamente).
  Por eso scripts/ingest_historical.py es la unica via soportada para
  NextGen (una fuente + --seasons con codigos de sede reales,
  explicitos) -- scripts/ingest_data.py lo rechaza (no hay "temporada
  actual" de NextGen, ver su propio build_source()).

PROBADO EN VIVO por el usuario en su propio equipo (temporadas E2024 y
U2024, ya cerradas): los cinco metodos funcionan contra la API real,
para Euroliga Y para EuroCup, sin ningun ajuste tras la primera
version -- a diferencia de nba.py, que necesito una correccion de
cabeceras despues de la primera prueba en vivo. Esto tambien confirma
en vivo, y ya no es una duda, que el endpoint de plantilla
(`clubs/{code}/people`) funciona igual para EuroCup ("U") que para
Euroliga ("E") -- se probo explicitamente el roster de un club de
EuroCup (Dreamland Gran Canaria) y devolvio 18 jugadores tipo "J"
correctamente. NextGen probado en vivo por separado el 2026-09-19 (ver
aviso "NEXTGEN (ANGT) RESUELTO EN VIVO" arriba) -- mismo resultado,
cero diferencias de formato.

RATE LIMIT REAL, CONFIRMADO EN VIVO (usuario, 2026-09-10) -- CORRIGE
una suposicion anterior de decisiones_proyecto.md ("Euroliga/EuroCup
no tienen ese limite forzado"): una ingesta historica de 5 temporadas
(script/ingest_historical.py) hizo que tanto live.euroleague.net/api
(Boxscore/Points) como api-live.euroleague.net/v2 (calendario)
empezaran a devolver 429 Too Many Requests tras ~150-180 peticiones
seguidas dentro de la misma sesion de ~40s -- y el bloqueo persistio
para el resto de esa ejecucion, incluidas temporadas y competiciones
(Euroliga Y EuroCup) totalmente distintas pedidas despues. No hay
Crawl-delay documentado para euroleague.net (a diferencia del `Crawl-
delay: 3` confirmado de basketball-reference.com, ver
sports_reference.py) ni se ha medido en vivo el umbral real -- por eso
este modulo NO forzaba ningun intervalo minimo entre peticiones al
principio (ver "PACING AÑADIDO" mas abajo para el cambio posterior);
lo que SI se corrigio aqui desde el principio es que un 429 real se
distinga de cualquier otro fallo: `_get_json()` lanza `RateLimitedError`
(la misma clase que sports_reference.py, importada de ahi -- es el
contrato que ya usa src/pipeline/ingest.py para "parar todo, no tratar
como un partido suelto que fallo") en vez de envolverlo en
`EuroleagueSourceError` generico. Antes de este cambio, un 429 en
pleno bucle de boxscores no paraba nada: `ingest.py` lo trataba como
"este partido en concreto fallo" y seguia intentando los cientos de
partidos restantes, cada uno con su propio 429 -- ver el caso real de
E2025 (83 boxscores ok, 319 "fallidos" uno a uno, todos 429, en vez de
abortar limpio tras el primero).

PACING AÑADIDO (2026-09-11), instruccion del usuario -- tras el fix de
arriba, el usuario reintento euroliga/eurocup TRES veces reales (ver
decisiones_proyecto.md, entrada "EUROLIGA/EUROCUP RATE LIMIT REAL"):
las tres chocaron con el bloqueo, pero cada una llego un poco mas lejos
en E2025 antes de bloquear (75 -> 100 -> 125 boxscores ok), con la
MISMA IP las tres veces y sin ninguna pausa entre peticiones (~150-180
peticiones seguidas en ~40s, es decir, un ritmo de ~4 peticiones/
segundo). Ese patron (mejora con el tiempo transcurrido, no con
cambios de IP) apunta a un limite que se va regenerando -- tipo
ventana deslizante -- mas que a un baneo fijo, aunque no hay
confirmacion en vivo de la mecanica exacta. `_get_json()` ahora fuerza
un intervalo minimo entre peticiones (mismo patron que
`SportsReferenceSession.get()` en sports_reference.py: espera lo que
haga falta antes de cada peticion, contando desde la peticion anterior
de ESTA MISMA instancia). El valor, `_MIN_INTERVAL_SECONDS = 1.5`
(~0.67 peticiones/segundo), sigue siendo una eleccion conservadora SIN
verificar en vivo -- no hay Crawl-delay documentado para euroleague.net
y el umbral real exacto se desconoce -- pero ya no es adivinar sin mas
contexto (ver "no adivinar" en decisiones_proyecto.md): es mas de 5
veces mas lento que el ritmo real (~4/s) que disparo el bloqueo en los
3 intentos sin pausa.

VALIDADO EN VIVO (2026-09-20) -- el usuario reintento
`ingest_historical.py --sources euroleague,eurocup` (las 8
combinaciones (fuente, temporada) por defecto, incluida E2025, la
misma temporada que bloqueo las tres veces anteriores) y confirmo que
la ingesta completo con exito, sin ningun 429. El pacing de
`_MIN_INTERVAL_SECONDS = 1.5` por si solo basta para evitar el
bloqueo -- ya no hace falta esperar tiempo adicional entre intentos
(no se cumplio la hipotesis de ventana temporal que habria exigido
mas que pacing). PENDIENTE (menor, no bloqueante): no se ha medido si
un intervalo mas corto que 1.5s tambien evitaria el bloqueo -- 1.5s
sigue siendo una eleccion conservadora, solo confirmada como
suficiente, no como el minimo real.
"""

from __future__ import annotations

import logging
import re
import time

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
# Misma clase que usa sports_reference.py (CBB/WNBA) -- src/pipeline/
# ingest.py la importa de ahi como el contrato de "parar todo, no
# reintentar" (ver docstring de ingest.py); reutilizarla aqui es lo que
# hace que un 429 real de euroleague.net dispare exactamente ese mismo
# comportamiento en vez de cualquier otro fallo generico. El nombre
# "SportsReferenceError" como clase base es un poco confuso fuera de
# ese modulo, pero mover la excepcion a un sitio mas neutral tocaria
# tambien sports_reference.py e ingest.py -- se deja como posible
# limpieza futura, no parte de este fix (ver decisiones_proyecto.md).
from src.scraper.sports_reference import RateLimitedError

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

# Intervalo minimo entre peticiones -- ver docstring del modulo,
# seccion "PACING AÑADIDO (2026-09-11)": valor conservador SIN
# verificar en vivo (no hay Crawl-delay documentado para
# euroleague.net), elegido para quedar bastante por debajo del ritmo
# real (~4 peticiones/segundo, sin pausa) que disparo el bloqueo en
# los 3 intentos reales del usuario.
_MIN_INTERVAL_SECONDS = 1.5

# competitionCode de la API v2, para las tres competiciones -- las tres
# confirmadas en vivo (ver aviso "NEXTGEN (ANGT) RESUELTO EN VIVO" mas
# arriba para el caso de NextGen, resuelto el 2026-09-19).
_COMPETITION_CODES = {
    "euroleague": "E",
    "eurocup": "U",
    "nextgen": "J",
}

# Prefijo de seasoncode esperado por competicion. Se usa solo como aviso
# temprano si alguien pasa un season con el prefijo equivocado; NextGen
# tiene bastantes mas variantes reales que "J" a secas (ver
# /v2/competitions/J/seasons, verificado en vivo 2026-09-19: JTB##
# Belgrado, JTC## Podgorica, JTD## Dubai, JTP## Paris, JTF4## Final
# Eight, y para años mas antiguos JT##/J####) -- en vez de listarlas
# todas (fragil si la API añade una sede nueva), basta con "J" solo:
# cubre a todas por prefijo comun y sigue rechazando un season que no
# sea de NextGen en absoluto.
_SEASON_PREFIXES = {
    "euroleague": ("E",),
    "eurocup": ("U",),
    "nextgen": ("J",),
}

_SHOT_ACTIONS = {"2FGA", "2FGM", "3FGA", "3FGM"}

# "DNP" (did not play) es literal en el campo Minutes de Boxscore; el
# resto llega como "MM:SS" (incluida la fila de totales, p. ej. "200:00").
def period_from_minute(minute) -> int | None:
    """Periodo (1-4 cuartos, 5+ prorrogas) a partir del minuto de juego de la API
    de Euroliga (campo MINUTE: 1-10 = 1er cuarto ... 31-40 = 4o; 41-45 = 1a prorroga,
    de 5 minutos cada una). None si el campo falta o no es un entero positivo."""
    try:
        m = int(str(minute).strip())
    except (TypeError, ValueError):
        return None
    if m <= 0:
        return None
    return (m - 1) // 10 + 1 if m <= 40 else 5 + (m - 41) // 5


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
        # Pacing entre peticiones (ver docstring del modulo, "PACING
        # AÑADIDO") -- por instancia, igual que SportsReferenceSession:
        # si se crean varias instancias no comparten el contador, asi
        # que una ingesta que use varias EuroleagueSource en paralelo
        # NO estaria realmente espaciando sus peticiones entre si.
        self._last_request_at: float | None = None

    # -- infraestructura HTTP ---------------------------------------------

    def _get_json(self, url: str, params: dict, context: str) -> dict:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            wait = _MIN_INTERVAL_SECONDS - elapsed
            if wait > 0:
                time.sleep(wait)

        try:
            response = self._session.get(url, params=params, timeout=_DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            self._last_request_at = time.monotonic()
            raise EuroleagueSourceError(f"Fallo pidiendo {context}: {exc}") from exc
        self._last_request_at = time.monotonic()

        # 429 se comprueba ANTES de raise_for_status() y por separado del
        # resto de errores HTTP -- ver aviso en el docstring del modulo
        # (2026-09-10): un 429 real de euroleague.net debe parar la
        # ingesta entera (RateLimitedError), no tratarse como el fallo de
        # un partido/temporada concreto que se avisa y se salta.
        if response.status_code == 429:
            raise RateLimitedError(
                f"429 Too Many Requests pidiendo {context} "
                f"({response.url}) -- posible bloqueo temporal de "
                f"IP/sesion en euroleague.net (ver docstring de "
                f"euroleague.py). PARAR el proceso por completo, no "
                f"reintentar de inmediato."
            )

        try:
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
                    period=period_from_minute(row.get("MINUTE")),
                )
                for row in rows
                if row.get("ID_ACTION") in _SHOT_ACTIONS
            ]
        except (KeyError, ValueError) as exc:
            raise EuroleagueSourceError(
                f"Points devolvio columnas o valores inesperados para "
                f"game_id={game_id!r}: {exc}"
            ) from exc
