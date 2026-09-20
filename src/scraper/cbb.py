"""Extractor de baloncesto universitario NCAA (CBB) sobre
sports-reference.com/cbb.

Implementa la interfaz comun definida en src/scraper/base.py
(BasketballSource). Quinto modulo de fuente del proyecto, y primero de
los dos que comparten el helper src/scraper/sports_reference.py (ver su
docstring para el detalle de la tecnica de tablas comentadas y del
limite de ritmo de peticiones -- CBB es la fuente mas estricta del
proyecto, ver plano seccion 10 y decisiones_proyecto.md).

CBB es una NON_TRAINABLE_SOURCE (ver base.py): sus datos NO pueden
usarse en train.py (prohibicion explicita y absoluta de Sports
Reference de usar su contenido para entrenar modelos de IA) -- si se
scrapean para EDA/visualizacion con atribucion, nunca para
entrenamiento.

VERIFICADO CONTRA PAGINAS REALES (con el navegador, no adivinado)
antes de escribir el mapeo de campos -- correcciones sobre lo que
documentaba el plano hasta ahora:

- El resumen de temporada NO esta en `/cbb/seasons/{men/women}/
  {year}-stats.html` (URL que documentaba el plano): esa URL devuelve
  404 real, confirmado en vivo. La URL correcta es
  `/cbb/seasons/{men/women}/{year}-school-stats.html` (tabla
  `basic_school_stats`, 398 escuelas D1 masculinas en 2023-24,
  confirmado con un fetch real) -- se usa como fuente de list_teams().
- NCAA tiene ramas separadas de hombres y mujeres con su propio
  calendario/plantilla/estadisticas (`/men/` y `/women/` en cada URL)
  -- no estaba documentado en el plano en absoluto. Este modulo lo
  expone como parametro `gender` del constructor ("men"/"women"), no
  como parte de la interfaz comun (que no distingue genero) -- mismo
  patron que `season` en NbaSource. `source` sigue siendo "cbb" para
  ambos (asi lo define base.py: una unica fuente, no dos).
- `list_games(season)` NO enumera equipo a equipo: hay ~360-400
  escuelas D1 solo en la rama masculina, y pedir el calendario de cada
  una (`/cbb/schools/{slug}/{gender}/{year}-schedule.html`) tardaria
  demasiado al ritmo impuesto por el rate limit (>20 minutos) y
  forzaria muchas mas peticiones de las necesarias. En su lugar usa el
  marcador diario `/cbb/boxscores/?month=M&day=D&year=Y` -- confirmado
  en vivo que devuelve HTML real con los partidos de ese dia (hombres
  Y mujeres juntos, distinguibles por `div.game_summary.gender-m` /
  `.gender-f`) con una peticion GET simple, Y CONFIRMADO EXPLICITAMENTE
  que esta permitido por robots.txt -- a diferencia de
  `/cbb/boxscores/index.cgi?*`, que SI esta bloqueado (`Disallow` real
  en robots.txt, confirmado con fetch). Itera dia a dia desde el 1 de
  noviembre del año anterior a `season` hasta el 30 de abril de
  `season` (convencion NCAA ya usada en las URLs de escuela: la
  temporada "2024" es 2023-24) -- unos 180 dias, ~180 peticiones a
  ~3.6s cada una (mas de 10 minutos reales, ver aviso de ritmo en
  sports_reference.py). Los dias sin partidos simplemente no aportan
  nada (0 `div.game_summary` de ese genero), no es un error.
- El enlace al boxscore de cada partido en el marcador diario
  (`td.gamelink a`) da el game_id REAL directamente
  (p. ej. "2023-11-06-21-duke", o "..._w" para partidos femeninos) --
  confirmado en vivo que su ultimo tramo (antes de un posible sufijo
  `_w`) es SIEMPRE el slug del equipo que la propia pagina considera
  local (probado con partidos en casa, fuera y en pista neutral: en
  pista neutral tambien usa un slug fijo, aunque ahi "local" no
  implica ventaja de campo real -- es solo la convencion de
  nomenclatura del sitio, se documenta tal cual). Por eso `game_id` NO
  se reconstruye a mano (evitando tener que adivinar la hora exacta de
  cada partido) -- se toma siempre del `href` real del marcador diario.
- Boxscore (`/cbb/boxscores/{game_id}.html`): las tablas
  `box-score-basic-{team_slug}` traen datos reales SIN necesitar el
  helper de tablas comentadas (confirmado en vivo, no estan
  comentadas) -- cabeceras `data-stat` reales: player, mp, fg, fga,
  fg_pct, fg2, fg2a, fg2_pct, fg3, fg3a, fg3_pct, ft, fta, ft_pct, orb,
  drb, trb, ast, stl, blk, tov, pf, pts, game_score. `mp` llega como
  minutos enteros en texto plano (p. ej. "31"), NO "MM:SS" -- a
  diferencia de WNBA (ver wnba.py). Los totales de equipo estan en un
  `<tfoot>` real con `data-stat="player"` = "School Totals". Una fila
  separadora `<tr class="thead">` con texto "Reserves" divide titulares
  de suplentes -- se descarta por la clase, igual que el resto de
  filas de cabecera repetidas tipicas de Sports Reference.
- `get_shots(game_id)`: CONFIRMADO EN VIVO que NO existe pagina de
  grafico de tiro para CBB (a diferencia de WNBA, ver wnba.py) --
  `/cbb/boxscores/shot-chart/{game_id}.html` devuelve 404 real, y la
  ficha del partido no trae ningun enlace "Shot Chart"/"Shot Charts"
  (se busco explicitamente). Lanza CbbSourceError, no un resultado
  vacio o inventado.

IDs usados en este modulo: `team_id` es el slug de escuela tal y como
aparece en la URL (p. ej. "duke", "north-carolina-central"), compartido
entre hombres y mujeres salvo que el propio nombre de escuela cambie.
`player_id` es el fragmento de URL completo de la ficha de jugador
(p. ej. "kyle-filipowski-1", con el sufijo numerico que Sports
Reference usa para desambiguar nombres repetidos -- NO es un ID
puramente numerico como en ACB/FEB). `game_id` es el fragmento de URL
completo del boxscore (p. ej. "2023-11-06-21-duke" o
"2023-11-06-19-louisiana-state_w" para partidos femeninos).
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

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

_BASE = "https://www.sports-reference.com"

_SCHOOL_HREF_RE = re.compile(r"/cbb/schools/([a-z0-9-]+)/")
_PLAYER_HREF_RE = re.compile(r"/cbb/players/([a-z0-9-]+)\.html")
# href real del marcador diario: fecha-hora-slug[_w].html (ver docstring del modulo).
_BOXSCORE_HREF_RE = re.compile(
    r"^/cbb/boxscores/(?P<gid>\d{4}-\d{2}-\d{2}-\d{1,2}-[a-z0-9-]+?)(?P<women>_w)?\.html$"
)


class CbbSourceError(SportsReferenceError):
    """Fallo al extraer datos de sports-reference.com/cbb, con
    contexto de que peticion concreta ha fallado y por que -- mismo
    estandar de codigo del proyecto que en los demas modulos de
    src/scraper/. Hereda de SportsReferenceError (ver
    sports_reference.py) para que quien orqueste el scraping pueda
    capturar tambien RateLimitedError con el mismo except si quiere."""


def _parse_minutes_plain(raw: object) -> float:
    """CBB da los minutos como entero en texto plano (p. ej. "31"),
    confirmado en vivo -- ver docstring del modulo. Nunca lanza: si no
    es un numero reconocible, registra un aviso y devuelve 0.0 (mismo
    criterio que el resto de modulos del proyecto ante un formato de
    minutos inesperado, p. ej. una fila "Did Not Play" sin confirmar
    en vivo para CBB pero manejada igual por si acaso)."""
    text = str(raw).strip()
    if not text or text.lower() in {"", "nan", "none", "did not play", "dnp"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        logger.warning("Formato de minutos no reconocido (CBB): %r; se registra como 0.0", raw)
        return 0.0


class CbbSource:
    source = "cbb"

    def __init__(self, gender: str = "men", season: str = "2024") -> None:
        """`gender`: "men" o "women" (ver docstring del modulo -- NCAA
        tiene ramas separadas, no es parte de la interfaz comun).
        `season`: año de referencia tipo "2024" para la temporada
        2023-24 (mismo formato que usan las URLs de escuela) -- se usa
        por defecto en list_players(), que la interfaz comun no deja
        pedir por parametro (mismo patron que NbaSource.season)."""
        if gender not in ("men", "women"):
            raise ValueError(f"gender debe ser 'men' o 'women', no {gender!r}")
        self.gender = gender
        self.season = season
        self._http = SportsReferenceSession()

    # -- equipos / jugadores ------------------------------------------------

    def list_teams(self, season: str) -> list[Team]:
        """Escuelas D1 de `season` (rama `self.gender`) segun el
        resumen de temporada real (ver docstring del modulo -- URL
        corregida respecto al plano)."""
        url = f"{_BASE}/cbb/seasons/{self.gender}/{season}-school-stats.html"
        response = self._http.get(url, context=f"listado de escuelas season={season!r} gender={self.gender!r}")
        table = extract_table(response.text, "basic_school_stats")
        if table is None:
            raise CbbSourceError(
                f"No se encontro la tabla 'basic_school_stats' en el "
                f"resumen de temporada season={season!r} gender={self.gender!r} "
                f"-- puede que el marcado haya cambiado (ver docstring del modulo)."
            )

        teams: dict[str, str] = {}
        for cell in table.select("td[data-stat=school_name]"):
            link = cell.find("a")
            if link is None:
                continue
            match = _SCHOOL_HREF_RE.search(link.get("href", ""))
            if not match:
                continue
            slug = match.group(1)
            name = link.get_text(strip=True)
            if name:
                teams[slug] = name

        if not teams:
            raise CbbSourceError(
                f"No se encontro ninguna escuela en el resumen de temporada "
                f"season={season!r} gender={self.gender!r} -- revisa el año."
            )
        return [Team(team_id=slug, name=name, source=self.source) for slug, name in teams.items()]

    def list_players(self, team_id: str) -> list[Player]:
        """Plantilla de `team_id` en `self.season`/`self.gender` (ver
        __init__ -- la interfaz comun no deja pedir season aqui)."""
        url = f"{_BASE}/cbb/schools/{team_id}/{self.gender}/{self.season}.html"
        response = self._http.get(
            url, context=f"plantilla de team_id={team_id!r} season={self.season!r} gender={self.gender!r}"
        )
        table = extract_table(response.text, "roster")
        if table is None:
            raise CbbSourceError(
                f"No se encontro la tabla 'roster' para team_id={team_id!r} "
                f"season={self.season!r} gender={self.gender!r} -- puede que "
                f"el equipo no exista esa temporada, o que el marcado haya "
                f"cambiado."
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
                # Respaldo si algun jugador no tuviera ficha propia
                # (confirmado que puede pasar en plantillas incompletas).
                player_id = f"{team_id}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
            players.append(Player(player_id=player_id, name=name, team_id=team_id, source=self.source))
        return players

    # -- partidos -------------------------------------------------------------

    def _list_games_for_day(self, day: date, season: str) -> list[GameRef]:
        url = f"{_BASE}/cbb/boxscores/?month={day.month}&day={day.day}&year={day.year}"
        response = self._http.get(url, context=f"marcador diario {day.isoformat()}")
        soup = BeautifulSoup(response.text, "html.parser")

        gender_class = "gender-m" if self.gender == "men" else "gender-f"
        games: list[GameRef] = []
        for game_div in soup.select(f"div.game_summary.{gender_class}"):
            gamelink = game_div.select_one("td.gamelink a")
            if gamelink is None:
                # Sin boxscore todavia (partido en directo o sin jugar) -- se omite.
                continue
            href_match = _BOXSCORE_HREF_RE.match(gamelink.get("href", ""))
            if not href_match:
                logger.warning(
                    "Marcador diario %s: enlace de boxscore con formato "
                    "inesperado (%r) -- se omite.",
                    day.isoformat(), gamelink.get("href"),
                )
                continue
            base_id = href_match.group("gid")
            game_id = base_id + (href_match.group("women") or "")

            team_rows = [
                row for row in game_div.select("table.teams tbody tr")
                if row.select_one(f"td a[href*='/cbb/schools/']")
            ]
            if len(team_rows) != 2:
                logger.warning(
                    "Marcador diario %s, game_id=%s: se esperaban 2 filas de "
                    "equipo, se encontraron %d -- se omite.",
                    day.isoformat(), game_id, len(team_rows),
                )
                continue

            parsed_teams = []
            for row in team_rows:
                link = row.select_one("td a[href*='/cbb/schools/']")
                slug_match = _SCHOOL_HREF_RE.search(link.get("href", ""))
                slug = slug_match.group(1) if slug_match else None
                cells = row.find_all("td")
                score = to_int(cells[1].get_text(strip=True), default=None) if len(cells) > 1 else None
                parsed_teams.append({"slug": slug, "score": score})

            if any(t["slug"] is None for t in parsed_teams):
                logger.warning(
                    "Marcador diario %s, game_id=%s: no se pudo extraer el "
                    "slug de algun equipo -- se omite.",
                    day.isoformat(), game_id,
                )
                continue

            # El ultimo tramo del game_id (antes de un posible "_w") es
            # SIEMPRE el slug del equipo local segun la propia pagina
            # (confirmado en vivo, ver docstring del modulo).
            home_idx = None
            for i, t in enumerate(parsed_teams):
                if base_id.endswith("-" + t["slug"]):
                    home_idx = i
                    break
            if home_idx is None:
                logger.warning(
                    "Marcador diario %s, game_id=%s: no se pudo determinar "
                    "el equipo local a partir del game_id -- se omite.",
                    day.isoformat(), game_id,
                )
                continue
            away_idx = 1 - home_idx

            games.append(
                GameRef(
                    game_id=game_id,
                    source=self.source,
                    season=season,
                    round=None,
                    date=day.isoformat(),
                    home_team_id=parsed_teams[home_idx]["slug"],
                    away_team_id=parsed_teams[away_idx]["slug"],
                    home_score=parsed_teams[home_idx]["score"],
                    away_score=parsed_teams[away_idx]["score"],
                    played=True,  # el marcador diario solo lista partidos ya jugados con boxscore
                )
            )
        return games

    def list_games(self, season: str) -> list[GameRef]:
        """Enumera los partidos de `self.gender` en `season`, dia a
        dia entre el 1 de noviembre del año anterior y el 30 de abril
        de `season` (ver docstring del modulo para el porque de este
        diseño y su coste real en peticiones/tiempo)."""
        try:
            season_year = int(season)
        except (TypeError, ValueError) as exc:
            raise CbbSourceError(
                f"season={season!r} debe ser un año tipo '2024' (temporada 2023-24)."
            ) from exc

        start = date(season_year - 1, 11, 1)
        end = date(season_year, 4, 30)
        total_days = (end - start).days + 1
        logger.info(
            "CbbSource.list_games(season=%r, gender=%r): recorriendo %d dias "
            "(%s a %s) al ritmo minimo de sports_reference.py -- esto tarda "
            "varios minutos, es el comportamiento esperado (ver docstring "
            "del modulo).",
            season, self.gender, total_days, start.isoformat(), end.isoformat(),
        )

        games: dict[str, GameRef] = {}
        current = start
        day_count = 0
        while current <= end:
            for game in self._list_games_for_day(current, season):
                games[game.game_id] = game
            day_count += 1
            if day_count % 30 == 0:
                logger.info(
                    "CbbSource.list_games: %d/%d dias procesados, %d partidos hasta ahora.",
                    day_count, total_days, len(games),
                )
            current += timedelta(days=1)
        return list(games.values())

    # -- boxscore / tiros -----------------------------------------------------

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        url = f"{_BASE}/cbb/boxscores/{game_id}.html"
        response = self._http.get(url, context=f"boxscore de game_id={game_id!r}")
        soup = BeautifulSoup(response.text, "html.parser")

        team_tables = [
            t for t in soup.find_all("table")
            if (t.get("id") or "").startswith("box-score-basic-")
        ]
        if len(team_tables) != 2:
            raise CbbSourceError(
                f"Se esperaban 2 tablas 'box-score-basic-*' en "
                f"game_id={game_id!r}, se encontraron {len(team_tables)}: "
                f"{[t.get('id') for t in team_tables]!r}."
            )

        team_stats: list[TeamStatLine] = []
        player_stats: list[PlayerStatLine] = []
        for table in team_tables:
            team_id = table["id"][len("box-score-basic-"):]
            try:
                body_rows = [
                    row for row in table.select("tbody tr")
                    if "thead" not in (row.get("class") or [])
                ]
                for row in body_rows:
                    player_cell = row.select_one("[data-stat=player]")
                    if player_cell is None:
                        continue
                    minutes_raw = row.select_one("[data-stat=mp]")
                    minutes = _parse_minutes_plain(minutes_raw.get_text(strip=True) if minutes_raw else "")
                    if minutes <= 0:
                        continue  # no jugo -- igual criterio que el resto del proyecto

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

                    player_stats.append(
                        PlayerStatLine(
                            game_id=game_id,
                            player_id=player_id,
                            player_name=player_name,
                            team_id=team_id,
                            minutes=minutes,
                            points=to_int(stat("pts")),
                            rebounds=to_int(stat("trb")),
                            assists=to_int(stat("ast")),
                            val=(
                                to_int(stat("pts")) + to_int(stat("trb")) + to_int(stat("ast"))
                                + to_int(stat("stl")) + to_int(stat("blk"))
                                - (to_int(stat("fga")) - to_int(stat("fg")))
                                - (to_int(stat("fta")) - to_int(stat("ft")))
                                - to_int(stat("tov")) - to_int(stat("pf"))
                            ),
                            plus_minus=None,  # CBB no publica +/- por jugador
                        )
                    )

                tfoot = table.find("tfoot")
                totals_row = tfoot.find("tr") if tfoot else None
                if totals_row is None:
                    raise CbbSourceError(
                        f"Sin <tfoot> de totales en la tabla de team_id={team_id!r} "
                        f"(game_id={game_id!r}) -- marcado inesperado."
                    )

                def totals_stat(name: str) -> str:
                    cell = totals_row.select_one(f"[data-stat={name}]")
                    return cell.get_text(strip=True) if cell else ""

                fg2_made, fg2_att = to_int(totals_stat("fg2")), to_int(totals_stat("fg2a"))
                fg3_made, fg3_att = to_int(totals_stat("fg3")), to_int(totals_stat("fg3a"))
                ft_made, ft_att = to_int(totals_stat("ft")), to_int(totals_stat("fta"))
                team_stats.append(
                    TeamStatLine(
                        game_id=game_id,
                        team_id=team_id,
                        t2_made=fg2_made, t2_att=fg2_att,
                        t3_made=fg3_made, t3_att=fg3_att,
                        ft_made=ft_made, ft_att=ft_att,
                        reb_off=to_int(totals_stat("orb")),
                        reb_def=to_int(totals_stat("drb")),
                        ast=to_int(totals_stat("ast")),
                        stl=to_int(totals_stat("stl")),
                        turnovers=to_int(totals_stat("tov")),
                        blocks=to_int(totals_stat("blk")),
                        fouls=to_int(totals_stat("pf")),
                        val=(
                            to_int(totals_stat("pts")) + to_int(totals_stat("trb"))
                            + to_int(totals_stat("ast")) + to_int(totals_stat("stl"))
                            + to_int(totals_stat("blk"))
                            - (to_int(totals_stat("fga")) - to_int(totals_stat("fg")))
                            - (ft_att - ft_made)
                            - to_int(totals_stat("tov")) - to_int(totals_stat("pf"))
                        ),
                    )
                )
            except KeyError as exc:
                raise CbbSourceError(
                    f"Tabla de estadisticas con columnas inesperadas para "
                    f"game_id={game_id!r}, team_id={team_id!r}: {exc}"
                ) from exc

        return BoxscoreData(game_id=game_id, team_stats=team_stats, player_stats=player_stats)

    def get_shots(self, game_id: str) -> list[Shot]:
        raise CbbSourceError(
            "get_shots() no esta implementado: se confirmo en vivo que "
            "sports-reference.com/cbb no tiene grafico de tiro para "
            "baloncesto universitario -- /cbb/boxscores/shot-chart/"
            "{game_id}.html devuelve 404 real y la ficha del partido no "
            "trae ningun enlace a esa pagina (a diferencia de WNBA, que "
            "SI lo tiene -- ver wnba.py). No es una limitacion de tiempo "
            "de exploracion, es una confirmacion de que el dato no "
            "existe ahi."
        )
