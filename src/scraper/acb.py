"""Extractor de la Liga Endesa (ACB).

Implementa la interfaz comun definida en src/scraper/base.py
(BasketballSource). Tercer modulo de fuente del proyecto.

VERIFICADO CONTRA PAGINAS REALES (via fetch) antes de escribir el
mapeo de campos -- mismo criterio que euroleague.py -- y con varias
correcciones importantes sobre lo que documentaba el plano hasta
ahora:

- El boxscore NO vive en acb.com bajo `/partido/ver/id/{ID}` (esa ruta,
  documentada originalmente en el plano, no se pudo confirmar). El
  boxscore real vive en un SUBDOMINIO DISTINTO, `live.acb.com`, con
  URLs del tipo `live.acb.com/partidos/{slug1}-vs-{slug2}-{id}/resumen`
  (resumen corto: Min/Pts/Rt/As/Fp/+/-/Val) y
  `live.acb.com/partidos/{slug1}-vs-{slug2}-{id}/estadisticas`
  (ficha completa, la que usa este modulo). Confirmado con un partido
  real ya jugado (Real Madrid 81-71 Dreamland Gran Canaria, 5 oct
  2025): la tabla llega como HTML real (no placeholders), apta para
  `pandas.read_html`.
- `/es/liga/clasificacion` y `/es/liga/partidos` en acb.com NO
  respetan `?editionId=N` como parametro de temporada -- se probo
  explicitamente y ambas siguen mostrando la temporada 2026-27 (sin
  empezar) sea cual sea el editionId de la query. `editionId` SI
  funciona como query param en las paginas de estadisticas/lideres y
  en las fichas de jugador (`.../partidos?editionId=N`) -- confirmado
  con datos reales de Facundo Campazzo, temporada 2025-26. Esto
  contradice la asuncion original del plano de que editionId
  funcionaba de forma uniforme en toda la web.
- Consecuencia directa: NO hay, de momento, una forma confirmada de
  pedir "dame todos los partidos de la temporada X" con una peticion
  GET simple. `list_games()` NO esta implementado por esta razon (ver
  mas abajo) en vez de adivinar un mecanismo sin verificar.
- `/es/liga/equipos` y las fichas de equipo (`/es/liga/equipos/{slug}-
  {id}`) SI devuelven HTML real (18 equipos, plantillas completas)
  con una peticion GET simple, sin editionId -- pero por eso mismo
  reflejan siempre la plantilla ACTUAL, no la de una temporada
  historica concreta.

IDs usados en este modulo (y por que no coinciden entre fuentes, igual
que ya pasaba en euroleague.py): `team_id` es el SLUG del equipo tal y
como aparece en la URL (p. ej. "real-madrid"), NUNCA el ID numerico que
tambien aparece ahi (p. ej. "9") -- se eligio el slug porque es la
unica parte que tambien aparece en las URLs de live.acb.com
(`real-madrid-vs-dreamland-gran-canaria-104464`); el ID numerico no
aparece ahi en absoluto. `player_id` SI es el ID numerico de la ficha
de jugador (p. ej. "20201774" para Sergio Llull), tal y como aparece
en `/es/liga/jugadores/{slug}-{id}`. `game_id` es el fragmento de URL
completo de live.acb.com (p. ej.
"real-madrid-vs-dreamland-gran-canaria-104464").

AVISO IMPORTANTE -- IDs de jugador NO coinciden entre list_players() y
get_boxscore(), igual que ya pasaba en euroleague.py: la tabla de
estadisticas de live.acb.com NO trae ningun ID de jugador, solo el
nombre abreviado tal y como se muestra en pantalla (p. ej. "S. Llull",
inicial + apellido) -- no el nombre completo ("Sergio Llull") que usa
list_players(), y desde luego no el ID numerico de su ficha
(p. ej. "20201774"). Por eso PlayerStatLine.player_id en este modulo
es ese nombre abreviado, no un ID real -- cualquier pipeline que
necesite cruzar boxscore con plantilla tendra que resolverlo por
nombre con un margen de error mayor que en euroleague.py (aqui ni
siquiera el formato del nombre coincide).

NO IMPLEMENTADO TODAVIA (por falta de verificacion, no por adivinar):
- `list_games(season)`: no hay endpoint confirmado para enumerar los
  partidos de una temporada con una peticion simple (ver arriba).
  Lanza AcbSourceError explicando el porque en vez de devolver datos
  incorrectos o vacios silenciosamente. Vias a explorar mas adelante:
  simular el PostBack/JS que cambia de jornada en `/es/liga/partidos`,
  o Playwright para ejecutar el JS de esa pagina.
- `get_shots(game_id)`: no se localizo un endpoint de coordenadas de
  tiro para ACB en el tiempo disponible (existe una vista "jugada a
  jugada" en live.acb.com, pero no se ha inspeccionado todavia). Lanza
  AcbSourceError igual que list_games().

SIN CONFIRMAR EN VIVO (a diferencia de euroleague.py, que si se pudo
probar con datos de una temporada completa): la asignacion de que
tabla de `estadisticas` corresponde a que equipo se asume por el orden
en que aparecen en la pagina, coincidiendo con el orden de `game_id`
(equipo1 primero) -- no se ha verificado que ese orden sea siempre
estable.

BUGS REALES ENCONTRADOS Y CORREGIDOS tras la primera prueba en vivo
del usuario (misma disciplina que con nba.py: no se adivino la causa,
se inspecciono el DOM real con un navegador antes de tocar el codigo):

- `list_teams()`/`list_players()` devolvian nombres pegados sin
  espacio, p. ej. "ASISA JOVENTUTJOV" o "Y. KraagAlero". Causa real:
  la tarjeta de equipo/jugador tiene el nombre y un segundo dato
  (siglas del equipo, o posicion del jugador) en dos elementos HTML
  hermanos dentro del mismo `<a>`, y `get_text(strip=True)` sobre todo
  el enlace concatena ambos sin separador. Corregido apuntando al
  elemento correcto: `h3` para el nombre de equipo (dentro de
  `a[data-testid='team-card-link']`, ignorando el enlace duplicado del
  menu de navegacion que no lleva texto), y al primer `<p>` para el
  nombre de jugador (el segundo `<p>` es la posicion, p. ej. "Alero",
  y se descarta).
- `get_boxscore()` devolvia TeamStatLine con todo el tiro a 0 y
  rebotes/valoracion absurdamente bajos (p. ej. val=3 para un equipo
  entero). Causa real, confirmada inspeccionando la tabla real: cada
  tabla de estadisticas tiene DOS filas especiales al final, "Equipo"
  (una fila de estadisticas de equipo sin jugador asociado, con tiro
  siempre a 0/0 y muy pocos rebotes/perdidas) y "Totales" (los
  totales reales del equipo) -- el filtro de deteccion de fila de
  totales incluia "equipo" como sinonimo de "totales" y, al coger la
  PRIMERA fila que encajaba, se quedaba con "Equipo" en vez de con
  "Totales" (aparece antes en la tabla). Corregido: "Equipo" sigue
  excluyendose de las filas de jugador (no es un jugador), pero ya NO
  se usa como fuente de los totales de equipo -- solo "Totales"/
  "Total" cuentan para eso. Ademas se detecto que live.acb.com a veces
  duplica la tabla completa de un equipo (mismo contenido exacto en
  dos `<table>` de la respuesta) -- `get_boxscore()` ahora deduplica
  las tablas por contenido antes de emparejarlas con slug1/slug2, en
  vez de asumir que las dos primeras tablas encontradas son siempre
  las dos correctas.
- La celda "Jugador" de cada fila de jugador incluye el dorsal pegado
  al nombre (p. ej. "33  G. Grinvalds", con doble espacio), no solo el
  nombre abreviado que describia el aviso de arriba. `player_id`/
  `player_name` ahora se limpian quitando ese prefijo numerico, para
  quedarse con el mismo formato "S. Llull" ya documentado.
"""

from __future__ import annotations

import io
import logging
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

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

_ACB_BASE = "https://www.acb.com"
_LIVE_BASE = "https://live.acb.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_DEFAULT_TIMEOUT = 30

# game_id = "{slug1}-vs-{slug2}-{numero}", tal y como lo usa live.acb.com.
_GAME_ID_RE = re.compile(r"^(?P<slug1>[a-z0-9-]+)-vs-(?P<slug2>[a-z0-9-]+)-(?P<numero>\d+)$")

# Columnas confirmadas en vivo de live.acb.com/partidos/.../estadisticas
# (ver docstring del modulo). "T2"/"T3"/"TL" llegan como "hechos/intentados".
_MADE_ATT_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class AcbSourceError(RuntimeError):
    """Fallo al extraer datos de acb.com/live.acb.com, con contexto de
    que peticion concreta ha fallado y por que -- mismo estandar de
    codigo del proyecto que NbaSourceError/EuroleagueSourceError:
    identificar el error concreto y relanzarlo con contexto, en vez de
    dejar que una excepcion generica pare la ejecucion en seco. Se usa
    tambien para las dos limitaciones documentadas arriba
    (list_games/get_shots sin endpoint confirmado), para que quien
    llame pueda distinguir "no implementado todavia" de un fallo de
    red real."""


def _made_attempted(raw: str) -> tuple[int, int]:
    """"3/5" -> (3, 5). Nunca lanza: si no encaja, registra un aviso y
    devuelve (0, 0) en vez de tirar abajo el resto de la fila."""
    match = _MADE_ATT_RE.match(str(raw))
    if not match:
        logger.warning("Formato de tiro no reconocido: %r; se registra como (0, 0)", raw)
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def _to_int(raw: object, default: int = 0) -> int:
    try:
        text = str(raw).strip()
        if not text or text in {"-", "—"}:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


# La celda "Jugador" de una fila de jugador real trae el dorsal pegado
# al nombre, p. ej. "33  G. Grinvalds" (confirmado en vivo -- ver
# docstring del modulo). Se quita ese prefijo para quedarse con el
# formato "S. Llull" que documenta el resto del modulo.
_JERSEY_PREFIX_RE = re.compile(r"^\d+\s+")


def _clean_player_cell(raw: object) -> str:
    return _JERSEY_PREFIX_RE.sub("", str(raw).strip()).strip()


class AcbSource:
    source = "acb"

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # -- infraestructura HTTP ---------------------------------------------

    def _get_soup(self, url: str, context: str) -> BeautifulSoup:
        try:
            response = self._session.get(url, timeout=_DEFAULT_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise AcbSourceError(f"Fallo pidiendo {context}: {exc}") from exc
        return BeautifulSoup(response.text, "html.parser")

    # -- equipos / jugadores ------------------------------------------------

    def list_teams(self, season: str) -> list[Team]:
        """Devuelve los equipos ACTUALES de la Liga Endesa (ver aviso
        del docstring del modulo: `/es/liga/equipos` no distingue
        temporada). `season` se acepta por la interfaz comun pero no
        tiene efecto todavia -- se ignora explicitamente, no en
        silencio: se registra un aviso si no es None."""
        if season:
            logger.warning(
                "AcbSource.list_teams(season=%r): /es/liga/equipos no "
                "distingue temporada via URL (ver docstring); se "
                "devuelven los equipos actuales, no los de esa season.",
                season,
            )
        soup = self._get_soup(f"{_ACB_BASE}/es/liga/equipos", context="listado de equipos")

        teams: dict[str, str] = {}
        try:
            # Solo las tarjetas de equipo reales (data-testid='team-card-link')
            # llevan el nombre en un <h3> -- el enlace del menu de navegacion
            # con el mismo href no tiene texto (ver docstring del modulo).
            for link in soup.select("a[data-testid='team-card-link'][href*='/es/liga/equipos/']"):
                href = link.get("href", "")
                match = re.search(r"/es/liga/equipos/([a-z0-9-]+)-(\d+)", href)
                if not match:
                    continue
                slug = match.group(1)
                name_tag = link.find("h3")
                name = (name_tag.get_text(strip=True) if name_tag else link.get_text(strip=True)) or slug
                if name:
                    teams[slug] = name
        except AttributeError as exc:
            raise AcbSourceError(
                f"Forma inesperada en /es/liga/equipos al extraer equipos: {exc}"
            ) from exc

        if not teams:
            raise AcbSourceError(
                "No se encontro ningun equipo en /es/liga/equipos -- "
                "es probable que la pagina haya cambiado de marcado."
            )
        return [Team(team_id=slug, name=name, source=self.source) for slug, name in teams.items()]

    def list_players(self, team_id: str) -> list[Player]:
        """Plantilla ACTUAL de `team_id` (slug, ver docstring del
        modulo). Resuelve primero el ID numerico real del equipo
        consultando /es/liga/equipos (los dos modulos comparten slug,
        pero solo esa pagina expone el ID numerico que necesita la URL
        de la ficha de equipo)."""
        team_url = self._resolve_team_url(team_id)
        soup = self._get_soup(team_url, context=f"ficha de equipo team_id={team_id!r}")

        players: dict[str, str] = {}
        try:
            for link in soup.select("a[href*='/es/liga/jugadores/']"):
                href = link.get("href", "")
                match = re.search(r"/es/liga/jugadores/([a-z0-9-]+)-(\d+)", href)
                if not match:
                    continue
                player_id = match.group(2)
                # La tarjeta de jugador tiene dos <p>: el primero es el
                # nombre, el segundo la posicion (p. ej. "Alero") -- coger
                # solo el primero evita el bug de nombre+posicion pegados
                # (ver docstring del modulo).
                name_tag = link.find("p")
                name = name_tag.get_text(strip=True) if name_tag else link.get_text(strip=True)
                if name:
                    players[player_id] = name
        except AttributeError as exc:
            raise AcbSourceError(
                f"Forma inesperada en la ficha de team_id={team_id!r} al "
                f"extraer jugadores: {exc}"
            ) from exc

        return [
            Player(player_id=pid, name=name, team_id=team_id, source=self.source)
            for pid, name in players.items()
        ]

    def _resolve_team_url(self, team_id: str) -> str:
        soup = self._get_soup(f"{_ACB_BASE}/es/liga/equipos", context="listado de equipos")
        for link in soup.select("a[href*='/es/liga/equipos/']"):
            href = link.get("href", "")
            if f"/es/liga/equipos/{team_id}-" in href:
                return href if href.startswith("http") else f"{_ACB_BASE}{href}"
        raise AcbSourceError(
            f"No se encontro team_id={team_id!r} en /es/liga/equipos -- "
            f"revisa que el slug sea correcto (viene de list_teams())."
        )

    # -- partidos -----------------------------------------------------------

    def list_games(self, season: str) -> list[GameRef]:
        raise AcbSourceError(
            "list_games() no esta implementado todavia: no hay una "
            "peticion GET simple confirmada que devuelva el calendario "
            "de una temporada -- ni /es/liga/partidos ni "
            "/es/liga/clasificacion respetan ?editionId= (verificado en "
            "vivo, ver docstring del modulo). Pendiente: simular el "
            "PostBack/JS que cambia de jornada, o usar Playwright."
        )

    # -- boxscore / tiros -----------------------------------------------------

    def _split_game_id(self, game_id: str) -> tuple[str, str, str]:
        match = _GAME_ID_RE.match(game_id)
        if not match:
            raise AcbSourceError(
                f"game_id={game_id!r} no tiene el formato esperado "
                f"'equipo1-vs-equipo2-numero' (el que usa live.acb.com)."
            )
        return match.group("slug1"), match.group("slug2"), match.group("numero")

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        slug1, slug2, _numero = self._split_game_id(game_id)
        url = f"{_LIVE_BASE}/partidos/{game_id}/estadisticas"
        try:
            response = self._session.get(url, timeout=_DEFAULT_TIMEOUT)
            response.raise_for_status()
            tables = pd.read_html(io.StringIO(response.text))
        except requests.RequestException as exc:
            raise AcbSourceError(f"Fallo pidiendo estadisticas de game_id={game_id!r}: {exc}") from exc
        except ValueError as exc:
            raise AcbSourceError(
                f"No se encontro ninguna tabla HTML en la ficha de "
                f"game_id={game_id!r}: {exc}"
            ) from exc

        candidate_tables = [df for df in tables if "Jugador" in df.columns]
        # live.acb.com a veces repite la tabla completa de un equipo dos
        # veces en la misma respuesta (mismo contenido exacto) -- confirmado
        # en vivo. Se deduplica por contenido antes de emparejar con
        # slug1/slug2, en vez de asumir que las dos primeras encontradas
        # son siempre las dos correctas.
        seen_csv: set[str] = set()
        stat_tables = []
        for df in candidate_tables:
            key = df.to_csv(index=False)
            if key in seen_csv:
                continue
            seen_csv.add(key)
            stat_tables.append(df)

        if len(stat_tables) < 2:
            raise AcbSourceError(
                f"Se esperaban 2 tablas de estadisticas (una por equipo, "
                f"tras deduplicar) en game_id={game_id!r}, se encontraron "
                f"{len(stat_tables)}. El marcado de live.acb.com puede "
                f"haber cambiado."
            )

        team_stats: list[TeamStatLine] = []
        player_stats: list[PlayerStatLine] = []
        for team_id, df in zip((slug1, slug2), stat_tables[:2]):
            try:
                jugador_lower = df["Jugador"].astype(str).str.strip().str.lower()
                # "Equipo" y "Totales"/"Total" son dos filas ESPECIALES
                # distintas (confirmado en vivo -- ver docstring del
                # modulo): "Equipo" son estadisticas sin jugador asociado
                # (tiro siempre 0/0), "Totales" son los totales reales del
                # equipo. Ambas se excluyen de las filas de jugador, pero
                # SOLO "Totales"/"Total" cuenta como fuente de los totales
                # de equipo -- coger la primera fila que encajara con
                # {"total","totales","equipo"} devolvia "Equipo" por error,
                # porque aparece antes en la tabla.
                non_player_mask = jugador_lower.isin({"total", "totales", "equipo"})
                real_totals_mask = jugador_lower.isin({"total", "totales"})
                totals_row = df[real_totals_mask]
                player_rows = df[~non_player_mask]

                t2_made_sum = t2_att_sum = t3_made_sum = t3_att_sum = 0
                ft_made_sum = ft_att_sum = ro_sum = rd_sum = as_sum = 0
                per_sum = rec_sum = tap_sum = tr_sum = fp_sum = fr_sum = val_sum = 0

                for _, row in player_rows.iterrows():
                    if str(row.get("Jugador", "")).strip() in {"", "nan"}:
                        continue
                    t2_made, t2_att = _made_attempted(row.get("T2", "0/0"))
                    t3_made, t3_att = _made_attempted(row.get("T3", "0/0"))
                    ft_made, ft_att = _made_attempted(row.get("TL", "0/0"))
                    minutes_raw = str(row.get("Min", "")).strip()
                    val = _to_int(row.get("Val"))

                    t2_made_sum += t2_made; t2_att_sum += t2_att
                    t3_made_sum += t3_made; t3_att_sum += t3_att
                    ft_made_sum += ft_made; ft_att_sum += ft_att
                    ro_sum += _to_int(row.get("RO")); rd_sum += _to_int(row.get("RD"))
                    as_sum += _to_int(row.get("As")); per_sum += _to_int(row.get("Pér"))
                    rec_sum += _to_int(row.get("Rec")); tap_sum += _to_int(row.get("Tap"))
                    tr_sum += _to_int(row.get("TR")); fp_sum += _to_int(row.get("Fp"))
                    fr_sum += _to_int(row.get("Fr")); val_sum += val

                    if minutes_raw and minutes_raw not in {"-", "nan", "0:00"}:
                        player_cell = _clean_player_cell(row.get("Jugador", ""))
                        player_stats.append(
                            PlayerStatLine(
                                game_id=game_id,
                                player_id=player_cell,
                                player_name=player_cell,
                                team_id=team_id,
                                minutes=_parse_minutes(minutes_raw),
                                points=_to_int(row.get("Pts")),
                                rebounds=_to_int(row.get("RT")),
                                assists=_to_int(row.get("As")),
                                val=val,
                                plus_minus=_to_int(row.get("+/-"), default=None) if "+/-" in df.columns else None,
                            )
                        )

                if not totals_row.empty:
                    trow = totals_row.iloc[0]
                    t2_made, t2_att = _made_attempted(trow.get("T2", "0/0"))
                    t3_made, t3_att = _made_attempted(trow.get("T3", "0/0"))
                    ft_made, ft_att = _made_attempted(trow.get("TL", "0/0"))
                    team_stats.append(
                        TeamStatLine(
                            game_id=game_id, team_id=team_id,
                            t2_made=t2_made, t2_att=t2_att,
                            t3_made=t3_made, t3_att=t3_att,
                            ft_made=ft_made, ft_att=ft_att,
                            reb_off=_to_int(trow.get("RO")), reb_def=_to_int(trow.get("RD")),
                            ast=_to_int(trow.get("As")), stl=_to_int(trow.get("Rec")),
                            turnovers=_to_int(trow.get("Pér")), blocks=_to_int(trow.get("Tap")),
                            fouls=_to_int(trow.get("Fp")), val=_to_int(trow.get("Val")),
                        )
                    )
                else:
                    logger.warning(
                        "game_id=%s, team_id=%s: sin fila de totales explicita "
                        "en la tabla; se calcula sumando las filas de "
                        "jugador (sin verificar contra el dato oficial).",
                        game_id, team_id,
                    )
                    team_stats.append(
                        TeamStatLine(
                            game_id=game_id, team_id=team_id,
                            t2_made=t2_made_sum, t2_att=t2_att_sum,
                            t3_made=t3_made_sum, t3_att=t3_att_sum,
                            ft_made=ft_made_sum, ft_att=ft_att_sum,
                            reb_off=ro_sum, reb_def=rd_sum,
                            ast=as_sum, stl=rec_sum,
                            turnovers=per_sum, blocks=tap_sum,
                            fouls=fp_sum, val=val_sum,
                        )
                    )
            except KeyError as exc:
                raise AcbSourceError(
                    f"Tabla de estadisticas con columnas inesperadas para "
                    f"game_id={game_id!r}, team_id={team_id!r}: {exc}"
                ) from exc

        return BoxscoreData(game_id=game_id, team_stats=team_stats, player_stats=player_stats)

    def get_shots(self, game_id: str) -> list[Shot]:
        raise AcbSourceError(
            "get_shots() no esta implementado todavia: no se localizo "
            "un endpoint de coordenadas de tiro para ACB en el tiempo "
            "disponible (live.acb.com tiene una vista 'jugada a jugada' "
            "sin inspeccionar todavia)."
        )


def _parse_minutes(raw: object) -> float:
    """"MM:SS" -> minutos en float. Igual que en euroleague.py/nba.py:
    nunca lanza, si no reconoce el formato devuelve 0.0 con un aviso."""
    text = str(raw).strip()
    if not text or text in {"-", "nan", "None"}:
        return 0.0
    if ":" in text:
        try:
            minutes_str, seconds_str = text.split(":", 1)
            return int(minutes_str) + int(seconds_str) / 60.0
        except ValueError:
            logger.warning("No se pudo parsear el campo de minutos %r", raw)
            return 0.0
    try:
        return float(text)
    except ValueError:
        logger.warning("Formato de minutos no reconocido: %r; se registra como 0.0", raw)
        return 0.0
