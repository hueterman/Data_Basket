"""Funciones de guardado idempotente sobre la base de datos normalizada.

"Idempotente" aqui significa: volver a ingerir la misma fuente/temporada
no duplica filas ni falla -- actualiza las que ya existian (UPSERT por la
clave primaria de cada tabla) o, para `shots` (sin clave natural, ver
`schema.py`), borra y vuelve a insertar por partido.

Estas funciones NO llaman a ningun scraper ni hacen I/O de red -- reciben
siempre las dataclasses ya obtenidas (de `src/scraper/base.py`, en vivo o
desde la cache de `data/raw/`) y solo escriben en SQLite. Esta separacion
es la que permite probarlas offline sin tocar la red (ver
`tests/test_pipeline_offline.py`) y es la misma disciplina "no adivinar"
ya aplicada al resto del proyecto: aqui no hay nada que adivinar, solo
tuberia de datos ya confirmados.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.scraper.base import (
    ALL_SOURCES,
    BoxscoreData,
    GameRef,
    Player,
    Shot,
    Team,
    is_trainable,
)


def upsert_sources(conn: sqlite3.Connection) -> None:
    """Rellena la tabla `sources` a partir de ALL_SOURCES/is_trainable.

    Se puede llamar tantas veces como haga falta (p. ej. al principio de
    cada ingesta) -- es un UPSERT sobre una lista fija y pequeña
    (ALL_SOURCES en src/scraper/base.py, que crece segun se añaden
    competiciones -- 15 a fecha de esta sesion tras la ampliacion FEB
    del 2026-09-20). Toda fuente/competicion real DEBE estar en
    ALL_SOURCES o nunca aparecera en la tabla `sources` (ni por tanto en
    el resumen de app/app.py) por mucho que se ingiera de verdad -- ver
    el aviso "BUG REAL corregido 2026-09-20(d)" en base.py.
    """
    rows = [(source, int(is_trainable(source))) for source in sorted(ALL_SOURCES)]
    conn.executemany(
        """
        INSERT INTO sources (source, trainable) VALUES (?, ?)
        ON CONFLICT (source) DO UPDATE SET trainable = excluded.trainable
        """,
        rows,
    )
    conn.commit()


def upsert_teams(conn: sqlite3.Connection, teams: list[Team]) -> int:
    if not teams:
        return 0
    rows = [(t.source, t.team_id, t.name) for t in teams]
    conn.executemany(
        """
        INSERT INTO teams (source, team_id, name) VALUES (?, ?, ?)
        ON CONFLICT (source, team_id) DO UPDATE SET name = excluded.name
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_players(conn: sqlite3.Connection, players: list[Player]) -> int:
    if not players:
        return 0
    rows = [(p.source, p.player_id, p.name, p.team_id) for p in players]
    conn.executemany(
        """
        INSERT INTO players (source, player_id, name, team_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (source, player_id) DO UPDATE SET
            name = excluded.name,
            team_id = excluded.team_id
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_games(conn: sqlite3.Connection, games: list[GameRef]) -> int:
    if not games:
        return 0
    rows = [
        (
            g.source, g.game_id, g.season, g.round, g.date,
            g.home_team_id, g.away_team_id,
            g.home_score, g.away_score, int(g.played),
        )
        for g in games
    ]
    conn.executemany(
        """
        INSERT INTO games (
            source, game_id, season, round, date,
            home_team_id, away_team_id, home_score, away_score, played
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, game_id) DO UPDATE SET
            season = excluded.season,
            round = excluded.round,
            date = excluded.date,
            home_team_id = excluded.home_team_id,
            away_team_id = excluded.away_team_id,
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            played = excluded.played
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_boxscore(
    conn: sqlite3.Connection, source: str, boxscore: BoxscoreData
) -> tuple[int, int]:
    """Guarda team_stats y player_stats de un boxscore. Devuelve
    (n_team_stats, n_player_stats)."""
    ts_rows = [
        (
            source, ts.game_id, ts.team_id,
            ts.t2_made, ts.t2_att, ts.t3_made, ts.t3_att,
            ts.ft_made, ts.ft_att, ts.reb_off, ts.reb_def,
            ts.ast, ts.stl, ts.turnovers, ts.blocks, ts.fouls, ts.val,
        )
        for ts in boxscore.team_stats
    ]
    if ts_rows:
        conn.executemany(
            """
            INSERT INTO team_stats (
                source, game_id, team_id,
                t2_made, t2_att, t3_made, t3_att,
                ft_made, ft_att, reb_off, reb_def,
                ast, stl, turnovers, blocks, fouls, val
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, game_id, team_id) DO UPDATE SET
                t2_made = excluded.t2_made, t2_att = excluded.t2_att,
                t3_made = excluded.t3_made, t3_att = excluded.t3_att,
                ft_made = excluded.ft_made, ft_att = excluded.ft_att,
                reb_off = excluded.reb_off, reb_def = excluded.reb_def,
                ast = excluded.ast, stl = excluded.stl,
                turnovers = excluded.turnovers, blocks = excluded.blocks,
                fouls = excluded.fouls, val = excluded.val
            """,
            ts_rows,
        )

    ps_rows = [
        (
            source, ps.game_id, ps.player_id, ps.player_name, ps.team_id,
            ps.minutes, ps.points, ps.rebounds, ps.assists,
            ps.val, ps.plus_minus,
        )
        for ps in boxscore.player_stats
    ]
    if ps_rows:
        conn.executemany(
            """
            INSERT INTO player_stats (
                source, game_id, player_id, player_name, team_id,
                minutes, points, rebounds, assists, val, plus_minus
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, game_id, player_id) DO UPDATE SET
                player_name = excluded.player_name,
                team_id = excluded.team_id,
                minutes = excluded.minutes,
                points = excluded.points,
                rebounds = excluded.rebounds,
                assists = excluded.assists,
                val = excluded.val,
                plus_minus = excluded.plus_minus
            """,
            ps_rows,
        )
    conn.commit()
    return len(ts_rows), len(ps_rows)


def replace_shots(
    conn: sqlite3.Connection, source: str, game_id: str, shots: list[Shot]
) -> int:
    """Sustituye TODOS los tiros de (source, game_id) por `shots`.

    Sin upsert fila a fila a proposito (ver docstring del modulo /
    schema.py): un tiro no tiene clave natural fiable.
    """
    conn.execute(
        "DELETE FROM shots WHERE source = ? AND game_id = ?",
        (source, game_id),
    )
    rows = [
        (source, s.game_id, s.player_id, s.team_id, s.loc_x, s.loc_y,
         int(s.made), s.shot_type)
        for s in shots
    ]
    if rows:
        conn.executemany(
            """
            INSERT INTO shots (
                source, game_id, player_id, team_id,
                loc_x, loc_y, made, shot_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def log_stage(
    conn: sqlite3.Connection,
    *,
    source: str,
    season: str,
    stage: str,
    n_items: int,
    n_errors: int = 0,
    notes: str = "",
    started_at: str,
) -> None:
    """Registra el resultado de una etapa de ingesta (ver `ingest.py`)."""
    conn.execute(
        """
        INSERT INTO ingest_log (
            source, season, stage, started_at, finished_at,
            n_items, n_errors, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source, season, stage, started_at,
            datetime.now(timezone.utc).isoformat(),
            n_items, n_errors, notes,
        ),
    )
    conn.commit()
