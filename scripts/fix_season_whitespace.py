"""Normaliza etiquetas de temporada ya guardadas ("AAAA-YY").

Corrige dos fallos de ingestas lanzadas con `--seasons` mal escrito:
  1. espacios sobrantes (" 2015-16"), por `--seasons "2014-15, 2015-16"`;
  2. fin incoherente ("2015-14", "2012-11", "2011-10"): el año de inicio es
     el que manda (acb.com pide la temporada por el año de inicio), asi que la
     etiqueta correcta es AAAA-(AAAA+1). En `games` solo se corrige si las
     FECHAS de los partidos confirman ese año de inicio (jul-dic = año,
     ene-jun = año+1); si no, se avisa y no se toca.

Tablas: `games` e `ingest_log`; ademas renombra carpetas de data/raw/<fuente>/.
Por defecto es un simulacro. Con --apply hace antes copia de seguridad.

    python scripts/fix_season_whitespace.py            # simulacro
    python scripts/fix_season_whitespace.py --apply
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

TABLES = ("games", "ingest_log")
_RE = re.compile(r"^(\d{4})-(\d{2})$")


def canonical(season: str) -> str:
    """Quita espacios y, si tiene forma AAAA-YY, fuerza el fin a AAAA+1."""
    s = season.strip()
    m = _RE.match(s)
    if not m:
        return s
    start = int(m.group(1))
    return f"{start}-{(start + 1) % 100:02d}"


def _date_start_year(date_iso: str) -> int | None:
    try:
        y, mth = int(date_iso[:4]), int(date_iso[5:7])
    except (ValueError, IndexError, TypeError):
        return None
    return y if mth >= 7 else y - 1


def games_confirmed(conn: sqlite3.Connection, source: str, season: str) -> bool:
    """True si las fechas de esos partidos casan con el año de inicio de la
    etiqueta corregida (al menos una fecha y todas coherentes)."""
    start = int(canonical(season)[:4])
    dates = [r[0] for r in conn.execute(
        "SELECT date FROM games WHERE source=? AND season=? AND date IS NOT NULL", (source, season))]
    years = [_date_start_year(d) for d in dates]
    years = [y for y in years if y is not None]
    return bool(years) and all(y == start for y in years)


def find_dirty(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    out = {}
    for t in TABLES:
        rows = [(s, se, n) for s, se, n in conn.execute(
            f"SELECT source, season, COUNT(*) FROM {t} GROUP BY 1, 2") if canonical(se) != se]
        if rows:
            out[t] = rows
    return out


def fix_db(conn: sqlite3.Connection) -> dict[str, int]:
    changed = {t: 0 for t in TABLES}
    for t, rows in find_dirty(conn).items():
        for source, season, _n in rows:
            if t == "games" and not games_confirmed(conn, source, season):
                continue                       # sin fechas que lo confirmen: no se toca
            cur = conn.execute(f"UPDATE {t} SET season=? WHERE source=? AND season=?",
                               (canonical(season), source, season))
            changed[t] += cur.rowcount
    conn.commit()
    return changed


def find_dirty_dirs(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(p for src in raw_dir.iterdir() if src.is_dir()
                  for p in src.iterdir() if p.is_dir() and canonical(p.name) != p.name)


def fix_dirs(dirs: list[Path]) -> list[str]:
    log = []
    for d in dirs:
        target = d.with_name(canonical(d.name))
        if target.exists():
            log.append(f"OMITIDA (ya existe {target.name}): {d}")
            continue
        d.rename(target)
        log.append(f"{d.name!r} -> {target.name!r}")
    return log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "processed" / "basketball.db")
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(args.db))
    dirty = find_dirty(conn)
    dirs = find_dirty_dirs(args.raw)
    if not dirty and not dirs:
        print("Nada que corregir.")
        return 0
    for t, rows in dirty.items():
        for src, season, n in rows:
            nota = ""
            if t == "games" and not games_confirmed(conn, src, season):
                nota = "  [NO SE TOCA: las fechas no confirman el año]"
            print(f"[BD] {t}: {src} {season!r} -> {canonical(season)!r} ({n} filas){nota}")
    for d in dirs:
        print(f"[cache] {d.parent.name}/{d.name!r} -> {canonical(d.name)!r}")
    if not args.apply:
        print("\nSimulacro: no se ha cambiado nada. Repite con --apply.")
        return 0

    from backup_db import make_backup
    dest = Path.home() / "backups" / "data_basket"
    print(f"Copia previa: {make_backup(args.db, dest)}")
    print("Filas actualizadas:", fix_db(conn))
    for line in fix_dirs(dirs):
        print("[cache]", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
