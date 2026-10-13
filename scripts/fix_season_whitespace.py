"""Quita espacios sobrantes de las etiquetas de temporada ya guardadas.

Causa: `--seasons "2014-15, 2015-16"` (con espacio tras la coma) guardaba la
temporada como " 2015-16". Rompe los filtros por temporada de la UI/API.
Corrige tablas `games` e `ingest_log` y renombra carpetas de data/raw/<fuente>/.

Por defecto NO cambia nada (solo muestra). Con --apply hace una copia de
seguridad de la BD antes y aplica los cambios.

    python scripts/fix_season_whitespace.py            # simulacro
    python scripts/fix_season_whitespace.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

TABLES = ("games", "ingest_log")


def find_dirty(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    out = {}
    for t in TABLES:
        rows = conn.execute(
            f"SELECT source, season, COUNT(*) FROM {t} WHERE season != TRIM(season) GROUP BY 1, 2"
        ).fetchall()
        if rows:
            out[t] = rows
    return out


def fix_db(conn: sqlite3.Connection) -> dict[str, int]:
    changed = {}
    for t in TABLES:
        cur = conn.execute(f"UPDATE {t} SET season = TRIM(season) WHERE season != TRIM(season)")
        changed[t] = cur.rowcount
    conn.commit()
    return changed


def find_dirty_dirs(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(p for src in raw_dir.iterdir() if src.is_dir()
                  for p in src.iterdir() if p.is_dir() and p.name != p.name.strip())


def fix_dirs(dirs: list[Path]) -> list[str]:
    log = []
    for d in dirs:
        target = d.with_name(d.name.strip())
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
            print(f"[BD] {t}: {src} {season!r} -> {season.strip()!r} ({n} filas)")
    for d in dirs:
        print(f"[cache] {d.parent.name}/{d.name!r}")
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
