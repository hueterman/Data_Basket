"""Esquema SQLite de la capa de almacenamiento normalizado.

Una unica base de datos (`data/processed/basketball.db`) para las ocho
fuentes del proyecto: la columna `source` distingue de donde viene cada
fila, en vez de tener una base de datos por fuente -- el objetivo de esta
capa es precisamente poder consultar/entrenar sobre las fuentes de forma
uniforme (ver plano, seccion 07 -- Esquema de datos, y seccion 09 -- Fases
del proyecto, fase 3).

Las columnas y tipos siguen al detalle los dataclasses de
`src/scraper/base.py` (Team, Player, GameRef, TeamStatLine,
PlayerStatLine, Shot) -- no hay traduccion de nombres de campo entre el
scraper y la base de datos, para que un cambio en uno se note enseguida
en el otro.

Decisiones de diseno que conviene tener presentes:

- Claves primarias compuestas por `(source, id...)`, nunca solo `id...`:
  `game_id`/`team_id`/`player_id` solo son unicos DENTRO de una fuente
  (ver docstrings de cbb.py/wnba.py sobre como se obtiene el game_id real
  del href, nunca reconstruido a mano) -- no hay garantia de que dos
  fuentes distintas no reutilicen el mismo id por casualidad.
- `teams`/`players` NO llevan `season`: los dataclasses Team/Player
  tampoco la llevan -- team_id/player_id se tratan como identidad estable
  entre temporadas (franquicia/escuela, jugador/a). `players.team_id` es
  el ULTIMO equipo visto en una llamada a list_players(), no un
  historico -- para saber en que equipo jugo un partido concreto, la
  fuente de verdad es `player_stats.team_id` (por partido), no esta
  tabla.
- Claves foraneas declaradas mas NO forzadas (no se activa
  `PRAGMA foreign_keys=ON`): son datos scrapeados de webs reales con
  casos raros (p. ej. un rival de exhibicion sin ficha propia en
  sports-reference.com/cbb, ya observado en las pruebas en vivo de esta
  sesion) -- una fila de boxscore valida no deberia perderse por no
  encajar con list_teams() al milimetro. Coherente con el criterio ya
  aplicado en el scraper: "avisa y omite, no revienta".
- `shots` no tiene clave natural (dos tiros del mismo jugador desde
  exactamente las mismas coordenadas en el mismo partido son posibles,
  p. ej. tiros libres) -- usa un id autoincremental y se reingesta
  borrando+insertando por (source, game_id) en vez de intentar upsert
  fila a fila.
"""

from __future__ import annotations

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS sources (
        source TEXT PRIMARY KEY,
        trainable INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS teams (
        source TEXT NOT NULL,
        team_id TEXT NOT NULL,
        name TEXT NOT NULL,
        PRIMARY KEY (source, team_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS players (
        source TEXT NOT NULL,
        player_id TEXT NOT NULL,
        name TEXT NOT NULL,
        team_id TEXT NOT NULL,
        PRIMARY KEY (source, player_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS games (
        source TEXT NOT NULL,
        game_id TEXT NOT NULL,
        season TEXT NOT NULL,
        round INTEGER,
        date TEXT,
        home_team_id TEXT NOT NULL,
        away_team_id TEXT NOT NULL,
        home_score INTEGER,
        away_score INTEGER,
        played INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (source, game_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_games_season
        ON games (source, season)
    """,
    """
    CREATE TABLE IF NOT EXISTS team_stats (
        source TEXT NOT NULL,
        game_id TEXT NOT NULL,
        team_id TEXT NOT NULL,
        t2_made INTEGER, t2_att INTEGER,
        t3_made INTEGER, t3_att INTEGER,
        ft_made INTEGER, ft_att INTEGER,
        reb_off INTEGER, reb_def INTEGER,
        ast INTEGER, stl INTEGER, turnovers INTEGER,
        blocks INTEGER, fouls INTEGER, val INTEGER,
        PRIMARY KEY (source, game_id, team_id),
        FOREIGN KEY (source, game_id) REFERENCES games (source, game_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_stats (
        source TEXT NOT NULL,
        game_id TEXT NOT NULL,
        player_id TEXT NOT NULL,
        player_name TEXT NOT NULL,
        team_id TEXT NOT NULL,
        minutes REAL,
        points INTEGER, rebounds INTEGER, assists INTEGER,
        val INTEGER, plus_minus INTEGER,
        PRIMARY KEY (source, game_id, player_id),
        FOREIGN KEY (source, game_id) REFERENCES games (source, game_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shots (
        shot_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        game_id TEXT NOT NULL,
        player_id TEXT NOT NULL,
        team_id TEXT NOT NULL,
        loc_x REAL NOT NULL,
        loc_y REAL NOT NULL,
        made INTEGER NOT NULL,
        shot_type TEXT,
        FOREIGN KEY (source, game_id) REFERENCES games (source, game_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_shots_game
        ON shots (source, game_id)
    """,
    # Añadido junto con la app Flask de exploracion de datos (app/app.py,
    # sesion 2026-09-18): la ficha de jugador filtra player_stats por
    # (source, player_id) sin pasar por game_id -- la PK de la tabla es
    # (source, game_id, player_id), que no sirve para ese filtro. Solo
    # acelera lecturas, no cambia ni una fila de dato ya guardado.
    """
    CREATE INDEX IF NOT EXISTS idx_player_stats_player
        ON player_stats (source, player_id)
    """,
    # Registro de ingestas: sobre todo pensado para CBB (>10 min reales
    # por temporada, ~180 peticiones a 3.6s) -- permite ver progreso y
    # confirmar que una etapa termino sin tener que fiarse de la consola.
    """
    CREATE TABLE IF NOT EXISTS ingest_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        season TEXT NOT NULL,
        stage TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        n_items INTEGER,
        n_errors INTEGER,
        notes TEXT
    )
    """,
)


def init_db(conn) -> None:
    """Crea las tablas/indices si no existen. Idempotente."""
    cur = conn.cursor()
    for statement in DDL_STATEMENTS:
        cur.execute(statement)
    conn.commit()
