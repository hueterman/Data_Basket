"""Extractor de la WNBA sobre basketball-reference.com/wnba.

Implementa la interfaz comun definida en src/scraper/base.py
(BasketballSource). Sexto y ultimo modulo de fuente del proyecto, y el
segundo (junto a cbb.py) que comparte el helper
src/scraper/sports_reference.py -- mismo motor exacto que CBB (HTML
estatico, tablas por `id`, celdas por `data-stat`, tablas secundarias
escondidas en comentarios), confirmado en vivo en la misma sesion que
escribio cbb.py (ver docstring de sports_reference.py para el detalle
tecnico completo, y decisiones_proyecto.md para el ritmo de peticiones
obligatorio).

WNBA es una NON_TRAINABLE_SOURCE (ver base.py): sus datos NO pueden
usarse en train.py (misma prohibicion de Sports Reference que CBB) --
se scrapean para EDA/visualizacion con atribucion, nunca para
entrenamiento.

VERIFICADO CONTRA PAGINAS REALES (con el navegador, no adivinado):

- Resumen de temporada: `/wnba/years/{season}.html`, tabla
  `wnba_standings` -- confirmado en vivo con la temporada 2024 (12
  equipos reales, con enlace a `/wnba/teams/{tricode}/{season}.html`
  cada uno). A diferencia del plano (que documentaba
  `/wnba/years/{año}.html` sin mas detalle), aqui se confirma el `id`
  real de la tabla y que trae los 12 equipos sin paginacion.
- Roster: `/wnba/teams/{tricode}/{season}.html`, tabla `roster` --
  columnas reales `number, player, pos, height, weight, birth_date,
  exp, college`; el nombre trae enlace a la ficha real de jugadora
  (`/wnba/players/{letra}/{id}.html`, p. ej. "bellki01w").
- Calendario de equipo: `/wnba/teams/{tricode}/{season}_games.html`,
  tabla `teams_games` (OJO: NO es `games`, que es el id que se
  esperaria por analogia con nba.com/stats -- confirmado en vivo el
  id real). Columnas reales: g, date_game (con el enlace real al
  boxscore), game_location ("" = en casa, "@" = fuera), opp_name,
  game_result, pts, opp_pts... `list_games(season)` en este modulo
  itera las plantillas de los 12 equipos (pocos, a diferencia de CBB)
  y combina sus calendarios, deduplicando por game_id -- cada partido
  aparece en el calendario de sus dos equipos.
- Boxscore: `/wnba/boxscores/{game_id}.html` (game_id real tomado del
  href del calendario, p. ej. "202405140LVA" -- YYYYMMDD + "0" +
  tricode del equipo LOCAL, confirmado en vivo cruzando con
  game_location de la propia fila). Tablas `box-{TRI}-game-basic` (NO
  comentadas, confirmado en vivo) -- OJO: el boxscore tambien trae
  tablas por cuarto/mitad (`box-{TRI}-q1-basic`, `-h1-basic`...) con
  el MISMO prefijo `box-{TRI}-`, asi que este modulo filtra
  especificamente por el sufijo `-game-basic` para no mezclarlas.
  Columnas reales: player, mp, fg, fga, fg_pct, fg3, fg3a, fg3_pct,
  ft, fta, ft_pct, orb, drb, trb, ast, stl, blk, tov, pf, pts,
  plus_minus -- SIN fg2/fg2a (a diferencia de CBB): t2_made/t2_att se
  derivan como fg-fg3 / fga-fg3a. `mp` llega como "MM:SS" (p. ej.
  "37:52"), a diferencia de CBB que usa minutos enteros en texto
  plano -- por eso este modulo NO reutiliza `_parse_minutes_plain` de
  cbb.py, tiene su propio parseo. Totales de equipo en un `<tfoot>`
  real (`data-stat="player"` = "Totals"). Misma fila separadora
  `<tr class="thead">` "Reserves" que en CBB.
- `get_shots(game_id)`: CONFIRMADO EN VIVO que SI existe, a diferencia
  de CBB -- `/wnba/boxscores/shot-chart/{game_id}.html` es una pagina
  real (200, no 404) con las coordenadas de cada tiro EMBEBIDAS
  DIRECTAMENTE en el HTML estatico (sin API/XHR aparte, a diferencia
  de FEB -- ver feb.py de esta misma sesion): dos contenedores
  `div#shots-{TRI}` (uno por equipo, TRI = tricode), cada uno con un
  `div.tooltip.q-{cuarto}.p-{player_id}.make|miss` por tiro, con
  `style="top:Npx;left:Npx;"` (posicion en pixeles sobre una imagen de
  medio campo de 500px de ancho, `cdn.ssref.net/req/1/images/bbr/
  court-wnba-bbr.svg`) y un atributo `tip` con el texto real del
  evento (p. ej. "1st quarter, 9:44.0 remaining<br>Diana Taurasi made
  3-pointer from 25 ft..."), de donde se extrae si fue un tiro de 2 o
  de 3 con una expresion regular sobre "N-pointer" (el acierto/fallo
  NO se lee de ahi, se lee de la clase `make`/`miss`, mas fiable).
  Confirmado con un partido real completo: 147 tiros (76+71), formato
  de clase 100% consistente en los dos equipos.

IDs usados en este modulo: `team_id` es el tricode de 3 letras tal y
como aparece en la URL (p. ej. "LVA", "NYL") -- estable, a diferencia
del slug largo de ACB o el ID numerico de FEB. `player_id` es el
fragmento de URL completo de la ficha de jugadora (p. ej.
"plumke01w"). `game_id` es el fragmento de URL completo del boxscore
(p. ej. "202405140LVA"), tomado siempre del href real del calendario,
nunca reconstruido a mano.
"""

from __future__ import annotations

import logging
import re

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
from src.scraper.sports_reference import (
    SportsReferenceError,
    SportsReferenceSession,
    extract_table,
    to_int,
)

logger = logging.getLogger(__name__)

_BASE = "https://www.basketball-reference.com"

_TEAM_HREF_RE = re.compile(r"/wnba/teams/([A-Z]+)/")
_PLAYER_HREF_RE = re.compile(r"/wnba/players/[a-z]/([a-z0-9]+)\.html")
_BOXSCORE_HREF_RE = re.compile(r"^/wnba/boxscores/(?P<gid>\d{9}[A-Z]{3})\.html$")
_SHOT_POS_RE = re.compile(r"top:\s*([\d.]+)px;\s*left:\s*([\d.]+)px")
_SHOT_TYPE_RE = re.compile(r"(\d)-pointer", re.IGNORECASE)


class WnbaSourceError(SportsReferenceError):
    """Fallo al extraer datos de basketball-reference.com/wnba, con
    contexto de que peticion concreta ha fallado y por que -- mismo
    estandar de codigo del proyecto que en los demas modulos de
    src/scraper/. Hereda de SportsReferenceError (ver
    sports_reference.py) para que quien orqueste el scraping pueda
    capturar tambien RateLimitedError con el mismo except si quiere."""


def _parse_minutes_colon(raw: object) -> float:
    """WNBA da los minutos como "MM:SS" (p. ej. "37:52"), confirmado
    en vivo -- ver docstring del modulo. Nunca lanza: si no encaja,
    registra un aviso y devuelve 0.0 (mismo criterio que el resto del
    proyecto; se conserva ademas el caso "Did Not Play"/vacio aunque
    no se ha visto un ejemplo real todavia, por si aparece en un
    partido con jugadoras inactivas)."""
    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none", "did not play", "dnp", "did not dress"}:
        return 0.0
    if ":" in text:
        try:
            minutes_str, seconds_str = text.split(":", 1)
            return int(minutes_str) + int(seconds_str) / 60.0
        except ValueError:
            logger.warning("No se pudo parsear el campo de minutos (WNBA): %r", raw)
            return 0.0
    try:
        return float(text)
    except ValueError:
        logger.warning("Formato de minutos no reconocido (WNBA): %r; se registra como 0.0", raw)
        return 0.0


class WnbaSource:
    source = "wnba"

    def __init__(self, season: str = "2024") -> None:
        """`season`: año de la temporada tal y como lo usan las URLs
        (p. ej. "2024") -- se usa por defecto en list_players(), que
        la interfaz comun no deja pedir por parametro (mismo patron
        que NbaSource.season)."""
        self.season = season
        self._http = SportsReferenceSession()

    # -- equipos / jugadoras ------------------------------------------------

    def list_teams(self, season: str) -> list[Team]:
        """Equipos de `season` segun el resumen de temporada real
        (tabla `wnba_standings`, ver docstring del modulo)."""
        url = f"{_BASE}/wnba/years/{season}.html"
        response = self._http.get(url, context=f"resumen de temporada season={season!r}")
        table = extract_table(response.text, "wnba_standings")
        if table is None:
            raise WnbaSourceError(
                f"No se encontro la tabla 'wnba_standings' para "
                f"season={season!r} -- puede que el marcado haya cambiado, "
                f"o que esa temporada no exista."
            )

        teams: dict[str, str] = {}
        for link in table.select("a[href*='/wnba/teams/']"):
            match = _TEAM_HREF_RE.search(link.get("href", ""))
            if not match:
                continue
            tricode = match.group(1)
            name = link.get_text(strip=True)
            if name:
                teams[tricode] = name

        if not teams:
            raise WnbaSourceError(
                f"No se encontro ningun equipo en el resumen de temporada "
                f"season={season!r}."
            )
        return [Team(team_id=tricode, name=name, source=self.source) for tricode, name in teams.items()]

    def list_players(self, team_id: str) -> list[Player]:
        """Plantilla de `team_id` (tricode) en `self.season` (ver
        __init__)."""
        url = f"{_BASE}/wnba/teams/{team_id}/{self.season}.html"
        response = self._http.get(
            url, context=f"plantilla de team_id={team_id!r} season={self.season!r}"
        )
        table = extract_table(response.text, "roster")
        if table is None:
            raise WnbaSourceError(
                f"No se encontro la tabla 'roster' para team_id={team_id!r} "
                f"season={self.season!r} -- puede que el equipo no exista "
                f"esa temporada, o que el marcado haya cambiado."
            )

        players: list[Player] = []
        for row in table.select("tbody tr"):
            name_cell = row.select_one("[data-stat=player]")
            if name_cell is None:
                continue
            name = name_cell.get_text(strip=True)
            if not name:
                continue
            link = name_cell.find("a")
            player_id = None
            if link is not None:
                match = _PLAYER_HREF_RE.search(link.get("href", ""))
                if match:
                    player_id = match.group(1)
            if player_id is None:
                player_id = f"{team_id}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
            players.append(Player(player_id=player_id, name=name, team_id=team_id, source=self.source))
        return players

    # -- partidos -------------------------------------------------------------

    def _list_games_for_team(self, team_id: str, season: str) -> list[GameRef]:
        url = f"{_BASE}/wnba/teams/{team_id}/{season}_games.html"
        response = self._http.get(
            url, context=f"calendario de team_id={team_id!r} season={season!r}"
        )
        table = extract_table(response.text, "teams_games")
        if table is None:
            logger.warning(
                "Sin tabla 'teams_games' para team_id=%s season=%s -- se "
                "omite (puede ser una temporada sin jugar todavia).",
                team_id, season,
            )
            return []

        games: list[GameRef] = []
        for row in table.select("tbody tr"):
            if "thead" in (row.get("class") or []):
                continue
            date_cell = row.select_one("[data-stat=date_game]")
            if date_cell is None:
                continue
            link = date_cell.find("a")
            if link is None:
                continue  # partido futuro, todavia sin boxscore
            match = _BOXSCORE_HREF_RE.match(link.get("href", ""))
            if not match:
                logger.warning(
                    "Calendario de team_id=%s: enlace de boxscore con "
                    "formato inesperado (%r) -- se omite.",
                    team_id, link.get("href"),
                )
                continue
            game_id = match.group("gid")

            def stat(name: str) -> str:
                cell = row.select_one(f"[data-stat={name}]")
                return cell.get_text(strip=True) if cell else ""

            location = stat("game_location")
            opp_link = row.select_one("[data-stat=opp_name] a")
            opp_match = _TEAM_HREF_RE.search(opp_link.get("href", "")) if opp_link else None
            opp_id = opp_match.group(1) if opp_match else None
            if opp_id is None:
                logger.warning(
                    "Calendario de team_id=%s, game_id=%s: no se pudo "
                    "extraer el tricode del rival -- se omite.",
                    team_id, game_id,
                )
                continue

            own_score = to_int(stat("pts"), default=None)
            opp_score = to_int(stat("opp_pts"), default=None)
            is_home = location != "@"  # "" = en casa, "@" = fuera (ver docstring del modulo)

            games.append(
                GameRef(
                    game_id=game_id,
                    source=self.source,
                    season=season,
                    round=None,
                    date=_parse_us_date(stat("date_game")),
                    home_team_id=team_id if is_home else opp_id,
                    away_team_id=opp_id if is_home else team_id,
                    home_score=own_score if is_home else opp_score,
                    away_score=opp_score if is_home else own_score,
                    played=own_score is not None,
                )
            )
        return games

    def list_games(self, season: str) -> list[GameRef]:
        """Enumera los partidos de `season` combinando el calendario
        de cada equipo (pocos, ~12-13 -- a diferencia de CBB, aqui SI
        es practico iterar equipo a equipo, ver docstring del modulo)
        y deduplicando por game_id (cada partido aparece en el
        calendario de sus dos equipos)."""
        teams = self.list_teams(season=season)
        games: dict[str, GameRef] = {}
        for team in teams:
            for game in self._list_games_for_team(team.team_id, season):
                games[game.game_id] = game
        return list(games.values())

    # -- boxscore / tiros -----------------------------------------------------

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        url = f"{_BASE}/wnba/boxscores/{game_id}.html"
        response = self._http.get(url, context=f"boxscore de game_id={game_id!r}")
        soup = BeautifulSoup(response.text, "html.parser")

        team_tables = [
            t for t in soup.find_all("table")
            if (t.get("id") or "").startswith("box-") and (t.get("id") or "").endswith("-game-basic")
        ]
        if len(team_tables) != 2:
            raise WnbaSourceError(
                f"Se esperaban 2 tablas 'box-{{TRI}}-game-basic' en "
                f"game_id={game_id!r}, se encontraron {len(team_tables)}: "
                f"{[t.get('id') for t in team_tables]!r}."
            )

        team_stats: list[TeamStatLine] = []
        player_stats: list[PlayerStatLine] = []
        for table in team_tables:
            # id real: "box-{TRI}-game-basic" -- el tricode es el tramo del medio.
            team_id = table["id"][len("box-"):-len("-game-basic")]
            try:
                body_rows = [
                    row for row in table.select("tbody tr")
                    if "thead" not in (row.get("class") or [])
                ]
                for row in body_rows:
                    player_cell = row.select_one("[data-stat=player]")
                    if player_cell is None:
                        continue
                    minutes_cell = row.select_one("[data-stat=mp]")
                    minutes = _parse_minutes_colon(minutes_cell.get_text(strip=True) if minutes_cell else "")
                    if minutes <= 0:
                        continue

                    link = player_cell.find("a")
                    player_id = None
                    if link is not None:
                        match = _PLAYER_HREF_RE.search(link.get("href", ""))
                        if match:
                            player_id = match.group(1)
                    player_name = player_cell.get_text(strip=True)
                    if player_id is None:
                        player_id = player_name

                    def stat(name: str) -> str:
                        cell = row.select_one(f"[data-stat={name}]")
                        return cell.get_text(strip=True) if cell else ""

                    fg_made, fg_att = to_int(stat("fg")), to_int(stat("fga"))
                    fg3_made, fg3_att = to_int(stat("fg3")), to_int(stat("fg3a"))
                    ft_made, ft_att = to_int(stat("ft")), to_int(stat("fta"))
                    pts, trb, ast = to_int(stat("pts")), to_int(stat("trb")), to_int(stat("ast"))
                    stl, blk = to_int(stat("stl")), to_int(stat("blk"))
                    tov, pf = to_int(stat("tov")), to_int(stat("pf"))
                    missed = (fg_att - fg_made) + (ft_att - ft_made)

                    player_stats.append(
                        PlayerStatLine(
                            game_id=game_id,
                            player_id=player_id,
                            player_name=player_name,
                            team_id=team_id,
                            minutes=minutes,
                            points=pts,
                            rebounds=trb,
                            assists=ast,
                            val=pts + trb + ast + stl + blk - missed - tov - pf,
                            plus_minus=to_int(stat("plus_minus"), default=None),
                        )
                    )

                tfoot = table.find("tfoot")
                totals_row = tfoot.find("tr") if tfoot else None
                if totals_row is None:
                    raise WnbaSourceError(
                        f"Sin <tfoot> de totales en la tabla de team_id={team_id!r} "
                        f"(game_id={game_id!r}) -- marcado inesperado."
                    )

                def totals_stat(name: str) -> str:
                    cell = totals_row.select_one(f"[data-stat={name}]")
                    return cell.get_text(strip=True) if cell else ""

                fg_made_t, fg_att_t = to_int(totals_stat("fg")), to_int(totals_stat("fga"))
                fg3_made_t, fg3_att_t = to_int(totals_stat("fg3")), to_int(totals_stat("fg3a"))
                ft_made_t, ft_att_t = to_int(totals_stat("ft")), to_int(totals_stat("fta"))
                pts_t, trb_t, ast_t = to_int(totals_stat("pts")), to_int(totals_stat("trb")), to_int(totals_stat("ast"))
                stl_t, blk_t = to_int(totals_stat("stl")), to_int(totals_stat("blk"))
                tov_t, pf_t = to_int(totals_stat("tov")), to_int(totals_stat("pf"))
                missed_t = (fg_att_t - fg_made_t) + (ft_att_t - ft_made_t)

                team_stats.append(
                    TeamStatLine(
                        game_id=game_id,
                        team_id=team_id,
                        # WNBA no da 2P directo (ver docstring del modulo) -- se deriva de FG-3P.
                        t2_made=fg_made_t - fg3_made_t,
                        t2_att=fg_att_t - fg3_att_t,
                        t3_made=fg3_made_t,
                        t3_att=fg3_att_t,
                        ft_made=ft_made_t,
                        ft_att=ft_att_t,
                        reb_off=to_int(totals_stat("orb")),
                        reb_def=to_int(totals_stat("drb")),
                        ast=ast_t,
                        stl=stl_t,
                        turnovers=tov_t,
                        blocks=blk_t,
                        fouls=pf_t,
                        val=pts_t + trb_t + ast_t + stl_t + blk_t - missed_t - tov_t - pf_t,
                    )
                )
            except KeyError as exc:
                raise WnbaSourceError(
                    f"Tabla de estadisticas con columnas inesperadas para "
                    f"game_id={game_id!r}, team_id={team_id!r}: {exc}"
                ) from exc

        return BoxscoreData(game_id=game_id, team_stats=team_stats, player_stats=player_stats)

    def get_shots(self, game_id: str) -> list[Shot]:
        """Coordenadas de tiro reales, embebidas directamente en el
        HTML estatico de la pagina de grafico de tiro -- ver docstring
        del modulo para el detalle completo confirmado en vivo. `x`/
        `y` quedan en pixeles sobre la imagen de medio campo de 500px
        que usa el sitio (unidad propia de esta fuente, sin normalizar
        -- la normalizacion entre fuentes es responsabilidad de
        src/pipeline/, mismo criterio que nba.py)."""
        url = f"{_BASE}/wnba/boxscores/shot-chart/{game_id}.html"
        response = self._http.get(url, context=f"grafico de tiro de game_id={game_id!r}")
        soup = BeautifulSoup(response.text, "html.parser")

        shot_areas = soup.select("div.shot-area[id^=shots-]")
        if not shot_areas:
            raise WnbaSourceError(
                f"No se encontro ningun 'div.shot-area' en el grafico de "
                f"tiro de game_id={game_id!r} -- puede que el marcado haya "
                f"cambiado, o que este partido no tenga grafico de tiro "
                f"disponible."
            )

        shots: list[Shot] = []
        for area in shot_areas:
            team_id = area["id"][len("shots-"):]
            for tooltip in area.select("div.tooltip"):
                classes = tooltip.get("class") or []
                quarter = next((c[2:] for c in classes if c.startswith("q-")), None)
                player_id = next((c[2:] for c in classes if c.startswith("p-")), None)
                made = "make" in classes
                if not ("make" in classes or "miss" in classes):
                    logger.warning(
                        "game_id=%s, team_id=%s: tiro sin clase make/miss "
                        "reconocible (%r) -- se omite.",
                        game_id, team_id, classes,
                    )
                    continue

                style = tooltip.get("style", "")
                pos_match = _SHOT_POS_RE.search(style)
                if not pos_match:
                    logger.warning(
                        "game_id=%s, team_id=%s: tiro sin posicion "
                        "reconocible en style=%r -- se omite.",
                        game_id, team_id, style,
                    )
                    continue
                loc_y, loc_x = float(pos_match.group(1)), float(pos_match.group(2))

                tip_text = tooltip.get("tip", "")
                type_match = _SHOT_TYPE_RE.search(tip_text)
                shot_type = type_match.group(1) if type_match else None

                shots.append(
                    Shot(
                        game_id=game_id,
                        player_id=player_id or "",
                        team_id=team_id,
                        loc_x=loc_x,
                        loc_y=loc_y,
                        made=made,
                        shot_type=shot_type,
                        period=int(quarter) if quarter and quarter.isdigit() else None,
                    )
                )
        return shots


_US_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_US_DATE_RE = re.compile(r"^\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})$")


def _parse_us_date(raw: str) -> str | None:
    """"Tue, May 14, 2024" -> "2024-05-14" (formato real confirmado en
    vivo en el calendario de equipo, ver docstring del modulo). Nunca
    lanza: si no encaja, devuelve None en vez de tirar abajo el resto
    de la fila."""
    match = _US_DATE_RE.match(raw.strip())
    if not match:
        return None
    month_name, day, year = match.groups()
    month = _US_MONTHS.get(month_name[:3])
    if month is None:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"
