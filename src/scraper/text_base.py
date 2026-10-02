"""Interfaz comun para los extractores de texto (declaraciones de
entrenadores, notas de partido).

Hermano de `base.py`: mismo espiritu -- una interfaz comun, cada fuente
normaliza sus propias particularidades por debajo -- pero para texto
libre en vez de estadisticas de caja. Ver decisiones_proyecto.md, seccion
"Modulo de lenguaje", para el contexto completo (herramienta INTERNA de
analisis, no pieza de portfolio publico).

Decision de alcance deliberada: una fuente de texto SIEMPRE referencia un
`game_id`/`team_id` que ya existen en `games`/`teams` (ingeridos antes
por un BasketballSource de este mismo paquete) -- nunca inventa su propia
numeracion de partidos ni de equipos. Es lo que permite cruzar
declaracion con resultado/margen/minutos sin ningun paso de conciliacion
aparte, y es la misma razon por la que TextSource (mas abajo) no
necesita sus propios list_teams()/list_games().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Vocabularios fijos (mismo espiritu que ALL_SOURCES/TRAINABLE_SOURCES en
# base.py): son decisiones de analisis, no datos scrapeados -- viven en
# codigo y no en una tabla, para que cambiarlos no sea una migracion de
# base de datos.
# ---------------------------------------------------------------------------

STATEMENT_TYPES: frozenset[str] = frozenset({
    "nota_institucional",   # redactada por el club/gabinete de comunicacion
    "declaracion_textual",  # cita literal del entrenador (rueda de prensa, canal propio)
})

AUTHOR_TYPES: frozenset[str] = frozenset({
    "club",        # redactor de club/gabinete de comunicacion
    "redactor",    # periodista o medio tercero
    "entrenador",  # el propio entrenador (canal oficial en primera persona)
})

# Taxonomia tactica de partida para `statement_concepts`: lista corta y
# deliberadamente editable, no exhaustiva desde el primer dia -- se
# ajustara con la validacion del gold set (fase de validacion del modulo).
# Incluye ya jerga real identificada en el articulo que motiva este
# modulo ("pegamento", "pierna", "profundidad de banquillo"): un modelo
# de sentimiento generico no la reconoce, asi que hace falta nombrarla
# explicitamente en vez de confiar en que el modelo la infiera.
TACTICAL_CONCEPTS: dict[str, tuple[str, ...]] = {
    "rotaciones": (
        "rotacion", "rotaciones", "gestion de minutos", "minutos",
    ),
    "defensa_bloqueo_directo": (
        "bloqueo directo", "bloqueo y continuacion", "pick and roll",
    ),
    "segundas_jugadas": (
        "segunda jugada", "segundas jugadas", "segundo tiro", "rebote ofensivo",
    ),
    "energia": (
        "energia", "intensidad", "pierna", "piernas",
    ),
    "profundidad_banquillo": (
        "profundidad de banquillo", "banquillo", "pegamento",
    ),
    "acierto_rival": (
        "el rival ha estado mas acertado", "porcentajes del rival",
        "acierto exterior del rival",
    ),
}


# ---------------------------------------------------------------------------
# Formas de datos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Coach:
    """Mismo PK compuesto y mismo caveat que Player en base.py: `team_id`
    es el ULTIMO equipo visto, no un historico -- para saber en que
    equipo estaba un entrenador en un partido concreto, la fuente de
    verdad es `statements.team_id` (por declaracion), no esta forma."""

    coach_id: str
    name: str
    team_id: str
    source: str


@dataclass(frozen=True)
class RawStatement:
    """Una declaracion o nota en crudo, tal como la devuelve una fuente de
    texto -- sin NLP todavia (eso lo hace `src/nlp/`, nunca el scraper,
    misma separacion que ya hay entre scraper y src/features.py).

    `source`/`game_id`/`team_id` deben apuntar a filas YA existentes en
    `games`/`teams` (ver docstring del modulo). `coach_id` puede ser None
    -- una nota de club puede ser un resumen de partido sin atribucion
    clara a un entrenador concreto; nunca se inventa una atribucion para
    rellenar el campo (ver schema.py, seccion "Modulo de lenguaje").

    `statement_type`/`author_type` son obligatorios y deben venir de
    STATEMENT_TYPES/AUTHOR_TYPES -- es la clasificacion que evita medir
    el estilo del gabinete de comunicacion en vez del discurso del
    entrenador (la primera leccion del articulo que motiva este modulo).
    """

    source: str
    game_id: str
    team_id: str
    statement_type: str  # uno de STATEMENT_TYPES
    author_type: str  # uno de AUTHOR_TYPES
    text: str
    coach_id: str | None = None
    url: str | None = None
    published_at: str | None = None  # ISO 8601; None si la fuente no lo da


# ---------------------------------------------------------------------------
# Interfaz comun
# ---------------------------------------------------------------------------

@runtime_checkable
class TextSource(Protocol):
    """Contrato que implementa cada modulo de fuente de texto en
    src/scraper/ (p. ej. feb_text.py, primera fuente prevista).

    Un solo metodo, a proposito: a diferencia de BasketballSource (que
    necesita listar equipos/jugadores/partidos antes de poder pedir un
    boxscore), una fuente de texto parte siempre de un game_id que YA
    viene de un BasketballSource ya ingerido -- list_teams()/
    list_players()/list_games() nunca se duplican aqui.
    """

    source: str  # identificador corto: "feb", "acb"...

    def list_statements(self, game_id: str) -> list[RawStatement]:
        """Declaraciones/notas encontradas para `game_id`.

        Lista vacia significa "la fuente no publico nada para ESTE
        partido concreto" (caso esperado y frecuente) -- no confundir con
        NotImplementedError, reservado para una fuente que nunca da texto
        en absoluto (mismo criterio que get_shots() en base.py)."""
        ...
