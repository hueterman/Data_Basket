"""Extractor de competiciones de la FEB (Federacion Espanola de
Baloncesto) via baloncestoenvivo.feb.es.

Implementa la interfaz comun definida en src/scraper/base.py
(BasketballSource). Cuarto modulo de fuente del proyecto.

Un objeto = una competicion concreta (p. ej. Primera FEB, LF Endesa,
Copa Espana...), identificada por los tres parametros reales que usa
`resultados.aspx` -- confirmados con fetch, no documentados
previamente en el plano con este detalle:

    https://baloncestoenvivo.feb.es/resultados.aspx?g={group_code}&t={year}&nm={slug}

    g=1  t=2026  nm=primerafeb   -> Primera FEB (masculina)
    g=4  t=2026  nm=lfendesa     -> Liga Femenina Endesa
    g=73 t=2026  nm=copaespaa    -> Copa Espana

VERIFICADO CONTRA PAGINAS REALES (via fetch) antes de escribir el
mapeo de campos:

- `resultados.aspx` SI devuelve HTML real con una peticion GET simple,
  sin necesidad de Playwright ni PostBack -- confirmado con partidos
  reales de Primera FEB 2026-27 (jornada 1, con enlaces reales a
  `Partido.aspx?p={id}`). Esto es mejor de lo esperado para acb.py
  (que no consiguio nada util con una peticion GET simple a su pagina
  de partidos).
- PERO el parametro `t=` (temporada) SE IGNORA: se probo
  explicitamente `t=2025` esperando la temporada 2025-26 ya terminada,
  y la pagina siguio mostrando 2026-27 (la que el servidor considera
  "actual"). Lo mismo con `j=` (jornada): tampoco tuvo efecto. Esto
  confirma lo que ya sospechaba el plano (PostBacks de ASP.NET
  necesarios para cambiar de jornada/temporada), pero para
  `resultados.aspx` en concreto -- list_games() en este modulo solo
  puede devolver lo que el servidor decida mostrar por defecto, no una
  temporada/jornada elegida por quien llama. Documentado explicitamente
  en list_games(), no adivinado.
- Boxscore (`Partido.aspx?p={id}`): NO se pudo verificar contra un
  partido ya jugado -- la temporada 2026-27 no ha empezado (todos los
  partidos muestran "*-*") y `t=`/parametros de temporada anterior no
  funcionan en resultados.aspx para encontrar uno ya jugado. La
  cabecera exacta de columnas de la tabla de estadisticas SIGUE SIN
  CONFIRMAR por esta via. Se usa el selector CSS ya confirmado por otra
  via en una sesion anterior (inspeccion manual del DOM real, ver
  decisiones_proyecto.md): contenedor `#divEstadisticas`/
  `.box-estadisticas`, tabla `table.tabla-datos`, fila de totales
  `tfoot tr` o `tr.totales`/`tr.totales-equipo`. La lectura de columnas
  se hace por texto de cabecera (`<th>`), no por posición fija, para
  ser mas resistente a un orden de columnas distinto del asumido --
  pero el mapeo de nombres de columna a los campos de PlayerStatLine
  (minutos/puntos/rebotes/asistencias/valoracion) esta basado en las
  mismas siglas que usa ACB (Min/Pts/RT/As/Val) por ser lo mas probable
  en un sitio de estadisticas de baloncesto espanol, SIN confirmar
  contra un ejemplo real -- pendiente de verificar en cuanto haya un
  partido jugado.

NO IMPLEMENTADO TODAVIA:
- `list_players(team_id)`: no se localizo una pagina de plantilla/
  roster por equipo dentro del tiempo disponible. Lanza
  FebSourceError explicando el motivo.
- `get_shots(game_id)`: la ficha de partido SI tiene una pestaña
  "Grafico de tiro" (confirmado via fetch, nombre real de la pestaña),
  lo que sugiere que existen coordenadas de tiro en algun punto -- pero
  no se identifico el endpoint/XHR que las sirve dentro del tiempo
  disponible. Lanza FebSourceError, no un resultado vacio o inventado.

BUG REAL ENCONTRADO Y CORREGIDO tras la primera prueba en vivo del
usuario: `_list_games_raw()` devolvia 0 partidos (todos los enlaces se
descartaban con "no se pudo separar equipos"). Causa real, confirmada
inspeccionando el DOM real con un navegador (no adivinada -- el fetch
por Markdown usado para verificar el resto del modulo perdia esta
estructura): la fila de cada partido en `resultados.aspx` NO tiene los
nombres de los dos equipos dentro del mismo enlace que el resultado,
como asumian los dos patrones de texto de la version anterior. Es una
fila de tabla ASP.NET con TRES enlaces y celdas separadas:

    <tr>
      <td>
        <a id="..._equipoLocalHyperLink" href=".../Equipo.aspx?i=1009713">NOMBRE EQUIPO LOCAL</a> -
        <a id="..._equipoVisitHyperLink" href=".../Equipo.aspx?i=1009072">NOMBRE EQUIPO VISITANTE</a>
      </td>
      <td><a id="..._resultadoHyperLink" href=".../Partido.aspx?p=2535727">*-*</a></td>
      <td>25/09/2026</td>
      <td>20:00</td>
    </tr>

(el resultado es literalmente el texto "*-*" cuando el partido aun no
se ha jugado -- de ahi el "texto='*-*'" que veia el aviso anterior; no
es un separador roto, es el marcador real de partido pendiente). Los
IDs de control ASP.NET (`_ctl2_`, `_ctl3_`...) cambian por fila, pero
el sufijo (`_equipoLocalHyperLink`, `_equipoVisitHyperLink`,
`_resultadoHyperLink`) es estable -- `_list_games_raw()` ahora
selecciona por ese sufijo (`a[id$='...']`) y sube al `<tr>` padre para
leer las tres celdas relacionadas, en vez de intentar separar nombres
del texto de un unico enlace con una expresion regular.

Esto ademas permitio una mejora real: los enlaces de equipo apuntan a
`Equipo.aspx?i={ID_NUMERICO}` -- un ID de equipo REAL de la FEB, no
inventado. `team_id` en este modulo usa ahora ese ID numerico
(p. ej. "1009713") en vez del slug derivado del nombre que se usaba
antes; `_slugify()` se mantiene solo como respaldo si algun enlace de
equipo no trajera ese parametro. La fecha (tercera celda, formato
"DD/MM/AAAA") se convierte a ISO 8601 para `GameRef.date`; el marcador
(segunda celda) se interpreta como jugado solo si es "numero-numero"
real, nunca si es "*-*" o cualquier otro texto no numerico.

IDs usados en este modulo: `team_id` es el ID numerico real de
`Equipo.aspx?i={ID}` (ver arriba); si no se puede extraer, se usa como
respaldo un slug derivado del nombre completo del equipo tal y como
aparece en resultados.aspx. `game_id` es el parametro numerico `p` de
Partido.aspx (p. ej. "2535727"), tal y como aparece en los enlaces
reales de resultados.aspx.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata

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

_BASE = "https://baloncestoenvivo.feb.es"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_DEFAULT_TIMEOUT = 30

# Nombres de columna esperados (SIN confirmar contra un partido real
# jugado, ver docstring del modulo) -- se acepta mas de un alias por
# columna porque no sabemos cual usa realmente el sitio.
_COLUMN_ALIASES = {
    "minutes": ["Min", "Minutos"],
    "points": ["Pts", "Puntos"],
    "rebounds": ["RT", "Reb", "Rebotes"],
    "assists": ["As", "Asis", "Asistencias"],
    "val": ["Val", "Valoracion", "Valoración"],
}

_MADE_ATT_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class FebSourceError(RuntimeError):
    """Fallo al extraer datos de baloncestoenvivo.feb.es, con contexto
    de que peticion concreta ha fallado y por que -- mismo estandar de
    codigo del proyecto que en los demas modulos de src/scraper/."""


def _slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "equipo"


def _to_int(raw: object, default: int = 0) -> int:
    try:
        text = str(raw).strip()
        if not text or text in {"-", "—", "nan"}:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _parse_minutes(raw: object) -> float:
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


def _pick_column(df_columns, aliases: list[str]) -> str | None:
    for alias in aliases:
        if alias in df_columns:
            return alias
    return None


# Equipo.aspx?i={ID} -- ID numerico real de equipo (ver docstring del modulo).
_TEAM_ID_RE = re.compile(r"[?&]i=(\d+)")

# Marcador real ("82-74") vs. placeholder de partido no jugado ("*-*").
_SCORE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")

# Fecha tal y como aparece en resultados.aspx: "25/09/2026" (DD/MM/AAAA).
_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")


def _team_id_from_href(href: str) -> str | None:
    match = _TEAM_ID_RE.search(href)
    return match.group(1) if match else None


def _parse_date(raw: str) -> str | None:
    match = _DATE_RE.match(raw)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


class FebSource:
    source = "feb"

    def __init__(self, group_code: str, year: str, slug: str):
        self.group_code = group_code
        self.year = year
        self.slug = slug
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # -- infraestructura HTTP ---------------------------------------------

    def _get_soup(self, url: str, context: str) -> BeautifulSoup:
        try:
            response = self._session.get(url, timeout=_DEFAULT_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FebSourceError(f"Fallo pidiendo {context}: {exc}") from exc
        return BeautifulSoup(response.text, "html.parser")

    def _resultados_url(self) -> str:
        return (
            f"{_BASE}/resultados.aspx?g={self.group_code}"
            f"&t={self.year}&nm={self.slug}"
        )

    # -- equipos / jugadores ------------------------------------------------

    def list_teams(self, season: str) -> list[Team]:
        """Deriva el listado de equipos de los partidos que devuelva
        resultados.aspx (ver aviso del docstring del modulo: `season`
        no tiene efecto, siempre es la jornada/temporada por defecto
        del servidor)."""
        if season:
            logger.warning(
                "FebSource.list_teams(season=%r): resultados.aspx no "
                "respeta el parametro de temporada (ver docstring); se "
                "devuelven los equipos de la jornada que el servidor "
                "muestre por defecto.",
                season,
            )
        games = self._list_games_raw()
        teams: dict[str, str] = {}
        for game in games:
            teams[game["home_team_id"]] = game["home_name"]
            teams[game["away_team_id"]] = game["away_name"]
        return [Team(team_id=tid, name=name, source=self.source) for tid, name in teams.items()]

    def list_players(self, team_id: str) -> list[Player]:
        raise FebSourceError(
            "list_players() no esta implementado todavia: no se "
            "localizo una pagina de plantilla/roster por equipo para "
            "esta competicion dentro del tiempo disponible."
        )

    # -- partidos -----------------------------------------------------------

    def _list_games_raw(self) -> list[dict]:
        """Estructura real confirmada con un navegador (ver docstring
        del modulo): cada partido es una fila <tr> con un enlace por
        equipo (`..._equipoLocalHyperLink` / `..._equipoVisitHyperLink`,
        con el ID numerico real del equipo en `?i=`) y un tercer enlace
        de resultado (`..._resultadoHyperLink`, texto "*-*" si el
        partido no se ha jugado). Se localiza por el sufijo estable del
        `id` (el prefijo `_ctlN_` de ASP.NET cambia por fila) y se sube
        al `<tr>` padre para leer las celdas relacionadas."""
        soup = self._get_soup(
            self._resultados_url(),
            context=f"resultados de g={self.group_code!r}, t={self.year!r}",
        )
        games: list[dict] = []
        try:
            for resultado_link in soup.select("a[id$='resultadoHyperLink']"):
                href = resultado_link.get("href", "")
                match = re.search(r"[?&]p=(\d+)", href)
                if not match:
                    continue
                game_id = match.group(1)

                row = resultado_link.find_parent("tr")
                if row is None:
                    logger.warning(
                        "Enlace de resultado para partido %s sin fila <tr> "
                        "padre -- se omite (marcado inesperado).", game_id,
                    )
                    continue

                home_link = row.select_one("a[id$='equipoLocalHyperLink']")
                away_link = row.select_one("a[id$='equipoVisitHyperLink']")
                if home_link is None or away_link is None:
                    logger.warning(
                        "Fila del partido %s sin los dos enlaces de equipo "
                        "esperados -- se omite (marcado inesperado).", game_id,
                    )
                    continue

                home_name = home_link.get_text(strip=True)
                away_name = away_link.get_text(strip=True)
                home_team_id = _team_id_from_href(home_link.get("href", "")) or _slugify(home_name)
                away_team_id = _team_id_from_href(away_link.get("href", "")) or _slugify(away_name)

                score_text = resultado_link.get_text(strip=True)
                score_match = _SCORE_RE.match(score_text)
                home_score = away_score = None
                played = False
                if score_match:
                    home_score, away_score = int(score_match.group(1)), int(score_match.group(2))
                    played = True

                cells = row.find_all("td")
                date_iso = _parse_date(cells[2].get_text(strip=True)) if len(cells) >= 3 else None

                games.append({
                    "game_id": game_id,
                    "home_name": home_name,
                    "away_name": away_name,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "date": date_iso,
                    "home_score": home_score,
                    "away_score": away_score,
                    "played": played,
                })
        except AttributeError as exc:
            raise FebSourceError(
                f"Forma inesperada en resultados.aspx (g={self.group_code!r}): {exc}"
            ) from exc
        return games

    def list_games(self, season: str) -> list[GameRef]:
        """SOLO devuelve la jornada/temporada que resultados.aspx
        muestre por defecto -- el parametro `season` se acepta por la
        interfaz comun pero no tiene efecto (ver docstring del
        modulo). `date`, `home_score`/`away_score` y `played` ya se
        extraen de la fila real de cada partido (ver
        `_list_games_raw()`); `round` sigue sin extraerse -- la fila no
        trae la jornada de forma directa, solo se sabe la que devuelve
        el servidor por defecto."""
        if season:
            logger.warning(
                "FebSource.list_games(season=%r): resultados.aspx no "
                "respeta el parametro de temporada; se devuelve la "
                "jornada que el servidor muestre por defecto.",
                season,
            )
        raw_games = self._list_games_raw()
        return [
            GameRef(
                game_id=g["game_id"],
                source=self.source,
                season=self.year,
                round=None,
                date=g["date"],
                home_team_id=g["home_team_id"],
                away_team_id=g["away_team_id"],
                home_score=g["home_score"],
                away_score=g["away_score"],
                played=g["played"],
            )
            for g in raw_games
        ]

    # -- boxscore / tiros -----------------------------------------------------

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        url = f"{_BASE}/Partido.aspx?p={game_id}"
        try:
            response = self._session.get(url, timeout=_DEFAULT_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FebSourceError(f"Fallo pidiendo acta de game_id={game_id!r}: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        tables_html = [str(t) for t in soup.select("table.tabla-datos")]
        if not tables_html:
            raise FebSourceError(
                f"No se encontro ninguna tabla table.tabla-datos en el "
                f"acta de game_id={game_id!r} -- puede que el partido "
                f"aun no se haya jugado, o que el selector CSS ya no "
                f"sea valido (ver docstring del modulo)."
            )

        try:
            dfs = [pd.read_html(io.StringIO(html))[0] for html in tables_html]
        except ValueError as exc:
            raise FebSourceError(
                f"No se pudo parsear table.tabla-datos como tabla en "
                f"game_id={game_id!r}: {exc}"
            ) from exc

        team_stats: list[TeamStatLine] = []
        player_stats: list[PlayerStatLine] = []
        for team_index, df in enumerate(dfs):
            team_id = f"equipo-{team_index + 1}"  # sin nombre de equipo fiable en esta tabla
            try:
                min_col = _pick_column(df.columns, _COLUMN_ALIASES["minutes"])
                pts_col = _pick_column(df.columns, _COLUMN_ALIASES["points"])
                reb_col = _pick_column(df.columns, _COLUMN_ALIASES["rebounds"])
                ast_col = _pick_column(df.columns, _COLUMN_ALIASES["assists"])
                val_col = _pick_column(df.columns, _COLUMN_ALIASES["val"])
                if not all([min_col, pts_col]):
                    raise FebSourceError(
                        f"La tabla de game_id={game_id!r} no tiene columnas "
                        f"reconocibles de minutos/puntos -- columnas reales: "
                        f"{list(df.columns)!r}. El mapeo de nombres de "
                        f"columna en este modulo esta sin confirmar contra "
                        f"un partido real (ver docstring); hay que "
                        f"ajustarlo con estas columnas."
                    )

                totals_mask = df.iloc[:, 0].astype(str).str.strip().str.lower().isin(
                    {"total", "totales", "equipo"}
                )
                player_rows = df[~totals_mask]

                for _, row in player_rows.iterrows():
                    minutes_raw = str(row.get(min_col, "")).strip()
                    if not minutes_raw or minutes_raw in {"-", "nan", "0:00"}:
                        continue
                    player_stats.append(
                        PlayerStatLine(
                            game_id=game_id,
                            player_id=str(row.iloc[0]).strip(),
                            player_name=str(row.iloc[0]).strip(),
                            team_id=team_id,
                            minutes=_parse_minutes(minutes_raw),
                            points=_to_int(row.get(pts_col)),
                            rebounds=_to_int(row.get(reb_col)) if reb_col else 0,
                            assists=_to_int(row.get(ast_col)) if ast_col else 0,
                            val=_to_int(row.get(val_col)) if val_col else 0,
                            plus_minus=None,
                        )
                    )

                totals_row = df[totals_mask]
                if not totals_row.empty:
                    trow = totals_row.iloc[0]
                    team_stats.append(
                        TeamStatLine(
                            game_id=game_id, team_id=team_id,
                            t2_made=0, t2_att=0, t3_made=0, t3_att=0,
                            ft_made=0, ft_att=0, reb_off=0, reb_def=0,
                            ast=_to_int(trow.get(ast_col)) if ast_col else 0,
                            stl=0, turnovers=0, blocks=0, fouls=0,
                            val=_to_int(trow.get(val_col)) if val_col else 0,
                        )
                    )
                    logger.warning(
                        "game_id=%s, team_id=%s: fila de totales encontrada "
                        "pero el desglose de tiros (T2/T3/TL) y rebotes "
                        "of/def no esta mapeado todavia (columnas reales sin "
                        "confirmar, ver docstring) -- TeamStatLine queda "
                        "incompleta a proposito, no con ceros silenciosos "
                        "sin avisar.", game_id, team_id,
                    )
            except KeyError as exc:
                raise FebSourceError(
                    f"Tabla de estadisticas con columnas inesperadas para "
                    f"game_id={game_id!r}: {exc}"
                ) from exc

        return BoxscoreData(game_id=game_id, team_stats=team_stats, player_stats=player_stats)

    def get_shots(self, game_id: str) -> list[Shot]:
        raise FebSourceError(
            "get_shots() no esta implementado todavia: la ficha de "
            "partido tiene una pestana 'Grafico de tiro' (confirmado "
            "via fetch), pero no se identifico el endpoint/XHR que "
            "sirve las coordenadas dentro del tiempo disponible."
        )
