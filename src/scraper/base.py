"""Interfaz comun para los extractores de datos de baloncesto.

Cada fuente (ACB, FEB, Euroleague Basketball -- Euroliga/EuroCup/NextGen --,
NBA, Sports Reference -- CBB/WNBA) implementa este mismo contrato en su
propio modulo (acb.py, feb.py, euroleague.py, nba.py, cbb.py, wnba.py),
normalizando internamente sus propias particularidades (PostBacks de
ASP.NET, tablas comentadas en HTML, prefijos de seasoncode, cabeceras
anti-bot...) antes de devolver siempre la misma forma de datos.

El resto del pipeline (almacenamiento, notebook, entrenamiento) llama
siempre a estos mismos metodos sin necesidad de saber que fuente hay
debajo. Ver el plano del proyecto, seccion 05 (Capa de scraping) y
seccion 07 (Esquema de datos), para el contexto completo de este diseno.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Identificadores de fuente y elegibilidad para entrenamiento
#
# Decision de alcance del proyecto (ver plano, seccion 10 -- Buenas
# practicas y aspectos legales): se scrapean las ocho fuentes sin
# excepcion, pero train.py excluye explicitamente cualquier fuente cuyos
# terminos de uso prohiban usar sus datos para entrenar modelos de IA.
# Hoy eso significa CBB y WNBA (Sports Reference): SI se scrapean y se
# usan para EDA/visualizacion con atribucion, pero NUNCA para entrenar.
# ---------------------------------------------------------------------------

TRAINABLE_SOURCES: frozenset[str] = frozenset({
    "acb", "feb", "euroleague", "eurocup", "nextgen", "nba",
    # Las demas competiciones de baloncestoenvivo.feb.es (FebSource,
    # ver src/scraper/feb.py) -- MISMO sitio y MISMOS terminos de uso
    # que "feb" (Primera FEB), asi que la misma clasificacion aplica.
    # BUG REAL corregido 2026-09-20(d) (detectado por el usuario: "no
    # aparecen las nuevas competiciones" en la pagina de resumen de la
    # app): estas entradas faltaban aqui -- upsert_sources() (ver
    # src/pipeline/repository.py) solo rellena la tabla `sources` (la
    # que lee app/app.py para el resumen) a partir de ALL_SOURCES, NO
    # de lo que realmente se haya ingerido, asi que una fuente ausente
    # de aqui nunca aparece en el resumen por mucho que se ingiera de
    # verdad. "lfendesa" tenia este mismo bug desde que se añadio
    # (sesion anterior, sin detectar hasta ahora); el resto son las
    # 6 añadidas hoy mismo (ver feb.py, "AMPLIACION 2026-09-20 (b)"/"(c)").
    "lfendesa", "lfchallenge", "lf2", "ligau", "copaespaa",
    "segundafeb", "tercerafeb",
})

NON_TRAINABLE_SOURCES: frozenset[str] = frozenset({
    "cbb", "wnba",
})

ALL_SOURCES: frozenset[str] = TRAINABLE_SOURCES | NON_TRAINABLE_SOURCES


def is_trainable(source: str) -> bool:
    """Indica si los datos de `source` pueden usarse en train.py.

    Lanza ValueError si `source` no es una fuente conocida del proyecto,
    en vez de devolver False en silencio -- un origen mal escrito no
    deberia colarse como "no entrenable" sin que nadie se entere.
    """
    if source not in ALL_SOURCES:
        raise ValueError(
            f"Fuente desconocida: {source!r}. Fuentes validas: "
            f"{sorted(ALL_SOURCES)}"
        )
    return source in TRAINABLE_SOURCES


# ---------------------------------------------------------------------------
# Formas de datos comunes (ver plano, seccion 07 -- Esquema de datos)
# ---------------------------------------------------------------------------

class BoxscoreNotPublished(RuntimeError):
    """La fuente NO publica estadisticas de este partido (la ficha existe,
    con marcador, pero sin tablas de jugadores). No es un fallo de lectura ni
    de red: reintentar no lo arregla. El pipeline lo cuenta aparte de los
    fallos reales (IngestSummary.n_boxscores_no_stats)."""


@dataclass(frozen=True)
class Team:
    team_id: str
    name: str
    source: str


@dataclass(frozen=True)
class Player:
    player_id: str
    name: str
    team_id: str
    source: str


@dataclass(frozen=True)
class GameRef:
    """Referencia minima a un partido: suficiente para listarlo antes de
    pedir su boxscore completo con get_boxscore()."""

    game_id: str
    source: str
    season: str
    round: int | None
    date: str | None  # ISO 8601 (YYYY-MM-DD); None si aun no esta confirmada
    home_team_id: str
    away_team_id: str
    home_score: int | None = None
    away_score: int | None = None
    played: bool = False


@dataclass(frozen=True)
class TeamStatLine:
    game_id: str
    team_id: str
    t2_made: int
    t2_att: int
    t3_made: int
    t3_att: int
    ft_made: int
    ft_att: int
    reb_off: int
    reb_def: int
    ast: int
    stl: int
    turnovers: int
    blocks: int
    fouls: int
    val: int  # PIR / Valoracion -- formula en el plano, seccion 07


@dataclass(frozen=True)
class PlayerStatLine:
    game_id: str
    player_id: str
    player_name: str
    team_id: str
    minutes: float
    points: int
    rebounds: int
    assists: int
    val: int
    plus_minus: int | None = None


@dataclass(frozen=True)
class BoxscoreData:
    game_id: str
    team_stats: list[TeamStatLine]
    player_stats: list[PlayerStatLine]


@dataclass(frozen=True)
class Shot:
    game_id: str
    player_id: str
    team_id: str
    loc_x: float
    loc_y: float
    made: bool
    shot_type: str | None = None  # "2", "3", "ft"...
    # Periodo del partido: 1-4 cuartos, 5+ prorrogas. None = la fuente no lo da
    # o el tiro se guardo antes de que se registrara (ver scripts/backfill_shot_periods.py).
    period: int | None = None


# ---------------------------------------------------------------------------
# Interfaz comun
# ---------------------------------------------------------------------------

@runtime_checkable
class BasketballSource(Protocol):
    """Contrato que implementa cada modulo de fuente en src/scraper/.

    Cada metodo debe devolver siempre las mismas formas de datos (Team,
    Player, GameRef, BoxscoreData, Shot) sin importar las particularidades
    internas de la fuente por debajo.

    get_shots() puede no estar disponible en todas las fuentes -- no toda
    competicion publica coordenadas de tiro. En ese caso la implementacion
    debe lanzar NotImplementedError con un mensaje claro, nunca devolver
    una lista vacia en silencio (una lista vacia significa "sin tiros en
    este partido", no "esta fuente no da tiros").
    """

    source: str  # identificador corto: "acb", "feb", "nba"...

    def list_teams(self, season: str) -> list[Team]:
        """Equipos que compitieron en `season`."""
        ...

    def list_players(self, team_id: str) -> list[Player]:
        """Jugadores/as de un equipo."""
        ...

    def list_games(self, season: str) -> list[GameRef]:
        """Todos los partidos de una temporada, jugados o por jugar."""
        ...

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        """Estadisticas de equipo y de jugador de un partido concreto."""
        ...

    def get_shots(self, game_id: str) -> list[Shot]:
        """Coordenadas de tiro de un partido, cuando la fuente lo ofrezca."""
        ...
