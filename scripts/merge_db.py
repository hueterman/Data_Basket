"""Fusiona OTRA base de datos (p. ej. la del PC) dentro de la principal (la de la Pi).

Regla: la base principal manda. Solo se AÑADE lo que le falta:
- partidos (source, game_id) que no existen en la principal, con su
  player_stats, team_stats y shots (los shot_id se regeneran, no se copian);
- partidos que en la principal estan SIN jugar (played=0) y en la otra YA
  jugados: se sustituyen por la version jugada;
- teams / players / coaches / sources que falten (INSERT OR IGNORE).
Nunca se tocan partidos que ya estan jugados en la principal. No copia
ingest_log ni statements*. Las temporadas se copian con .strip().

Por defecto es un simulacro. Con --apply hace antes copia de seguridad de la
principal y aplica todo en UNA transaccion (si falla algo, no cambia nada).

    python scripts/merge_db.py --other /ruta/pc.db            # simulacro
    python scripts/merge_db.py --other /ruta/pc.db --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

GAME_COLS = ("source, game_id, season, round, date, home_team_id, away_team_id, "
             "home_score, away_score, played")
SHOT_COLS = "source, game_id, player_id, team_id, loc_x, loc_y, made, shot_type"


def _cols(conn, table, schema="main"):
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def plan(conn: sqlite3.Connection) -> tuple[list[tuple], list[tuple]]:
    """(juegos nuevos, juegos a sustituir) como listas de (source, game_id)."""
    new = conn.execute(
        """SELECT o.source, o.game_id FROM other.games o
           LEFT JOIN main.games m ON m.source=o.source AND m.game_id=o.game_id
           WHERE m.game_id IS NULL"""
    ).fetchall()
    upd = conn.execute(
        """SELECT o.source, o.game_id FROM other.games o
           JOIN main.games m ON m.source=o.source AND m.game_id=o.game_id
           WHERE m.played=0 AND o.played=1"""
    ).fetchall()
    return new, upd


def merge(conn: sqlite3.Connection) -> dict[str, int]:
    """Aplica la fusion (conn con `other` adjunta). El llamador hace commit."""
    conn.execute("DROP TABLE IF EXISTS temp._todo")
    conn.execute("CREATE TEMP TABLE _todo(source TEXT, game_id TEXT, PRIMARY KEY(source, game_id))")
    new, upd = plan(conn)
    conn.executemany("INSERT INTO _todo VALUES (?,?)", new + upd)

    res = {"games_nuevos": len(new), "games_sustituidos": len(upd)}
    for t in ("shots", "player_stats", "team_stats"):
        conn.execute(f"DELETE FROM main.{t} WHERE (source, game_id) IN "
                     f"(SELECT source, game_id FROM _todo WHERE (source, game_id) IN "
                     f"(SELECT source, game_id FROM main.games))")
    conn.execute("DELETE FROM main.games WHERE (source, game_id) IN "
                 "(SELECT m.source, m.game_id FROM _todo m JOIN main.games g "
                 "ON g.source=m.source AND g.game_id=m.game_id)")

    conn.execute(f"""INSERT INTO main.games ({GAME_COLS})
        SELECT o.source, o.game_id, TRIM(o.season), o.round, o.date, o.home_team_id,
               o.away_team_id, o.home_score, o.away_score, o.played
        FROM other.games o JOIN _todo t ON t.source=o.source AND t.game_id=o.game_id""")
    for t in ("player_stats", "team_stats"):
        cols = ", ".join(_cols(conn, t))
        res[t] = conn.execute(
            f"INSERT INTO main.{t} ({cols}) SELECT {', '.join('o.'+c for c in _cols(conn, t))} "
            f"FROM other.{t} o JOIN _todo x ON x.source=o.source AND x.game_id=o.game_id"
        ).rowcount
    res["shots"] = conn.execute(
        f"INSERT INTO main.shots ({SHOT_COLS}) SELECT {', '.join('o.'+c for c in SHOT_COLS.split(', '))} "
        f"FROM other.shots o JOIN _todo x ON x.source=o.source AND x.game_id=o.game_id"
    ).rowcount
    for t in ("teams", "players", "coaches", "sources"):
        cols = ", ".join(_cols(conn, t))
        res[t] = conn.execute(
            f"INSERT OR IGNORE INTO main.{t} ({cols}) SELECT {cols} FROM other.{t}"
        ).rowcount
    conn.execute("DROP TABLE temp._todo")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--other", type=Path, required=True, help="BD a incorporar (solo lectura)")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "processed" / "basketball.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if not args.other.is_file() or not args.db.is_file():
        print("ERROR: no existe la BD principal o la otra.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(args.db), timeout=120)
    conn.execute("ATTACH DATABASE ? AS other", (str(args.other),))
    new, upd = plan(conn)
    print(f"Partidos nuevos a incorporar: {len(new)} | sin jugar -> jugados: {len(upd)}")
    by = conn.execute("""SELECT o.source, TRIM(o.season), COUNT(*) FROM other.games o
        LEFT JOIN main.games m ON m.source=o.source AND m.game_id=o.game_id
        WHERE m.game_id IS NULL OR (m.played=0 AND o.played=1) GROUP BY 1,2 ORDER BY 1,2""").fetchall()
    for s, se, n in by:
        print(f"  {s:12s} {se:9s} {n:5d}")
    if not args.apply:
        print("\nSimulacro: no se ha cambiado nada. Repite con --apply.")
        return 0
    if not new and not upd:
        print("Nada que fusionar.")
        return 0

    from backup_db import make_backup
    print("Copia previa:", make_backup(args.db, Path.home() / "backups" / "data_basket"), flush=True)
    try:
        conn.execute("BEGIN")
        res = merge(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print("Hecho:", res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
