"""Copia de seguridad consistente de data/processed/basketball.db.

Pensado para la Raspberry Pi (ver deploy/raspberry/databasket-backup.timer),
pero funciona igual en cualquier equipo.

POR QUE ASI:
- Usa la API de backup de SQLite (sqlite3.Connection.backup), NO `cp`: la base de
  datos esta en modo WAL y la API o la ingesta pueden estar escribiendo a la vez;
  copiar el fichero a pelo puede dar una copia corrupta. Esta copia es consistente
  aunque haya una ingesta en marcha.
- Comprueba la copia (PRAGMA integrity_check) ANTES de darla por buena: una copia
  que no se ha verificado no es una copia de seguridad.
- Comprime con gzip (la BD pesa ~300 MB) y escribe de forma atomica (.tmp ->
  renombrado): nunca queda un fichero a medias con aspecto de copia valida.
- Retencion (por defecto): la mas reciente de cada uno de los ultimos 7 dias,
  de las ultimas 4 semanas y de los ultimos 3 meses. Borra el resto.

Uso:
    python scripts/backup_db.py                  # copia + poda
    python scripts/backup_db.py --list           # ver copias existentes
    python scripts/backup_db.py --verify         # comprobar la copia mas reciente
    python scripts/backup_db.py --dest /mnt/usb/backups --db otra.db

Destino por defecto: ~/backups/data_basket  (o la variable de entorno BACKUP_DIR).
IMPORTANTE: si es el MISMO disco que la base de datos, protege de borrados, errores
y corrupcion, pero NO de que el disco falle -- baja copias a otro equipo (ver
deploy/raspberry/README.md).
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "processed" / "basketball.db"
DEFAULT_DEST = Path(os.environ.get("BACKUP_DIR", str(Path.home() / "backups" / "data_basket")))

PREFIX = "basketball_"
SUFFIX = ".db.gz"
_STAMP = "%Y-%m-%d_%H%M"


class BackupError(RuntimeError):
    pass


def parse_backup_name(path: Path) -> datetime | None:
    """Fecha de una copia a partir de su nombre; None si no es una copia nuestra."""
    name = path.name
    if not (name.startswith(PREFIX) and name.endswith(SUFFIX)):
        return None
    try:
        return datetime.strptime(name[len(PREFIX):-len(SUFFIX)], _STAMP)
    except ValueError:
        return None


def list_backups(dest_dir: Path) -> list[tuple[datetime, Path]]:
    if not dest_dir.is_dir():
        return []
    found = [(parse_backup_name(p), p) for p in dest_dir.iterdir()]
    return sorted(((dt, p) for dt, p in found if dt is not None), key=lambda e: e[0], reverse=True)


def select_to_keep(entries: list[tuple[datetime, Path]], daily: int = 7, weekly: int = 4,
                   monthly: int = 3) -> set[Path]:
    """Que copias se conservan: la mas reciente de cada uno de los ultimos `daily`
    dias, `weekly` semanas ISO y `monthly` meses (con copias). Union de las tres."""
    ordered = sorted(entries, key=lambda e: e[0], reverse=True)
    keep: set[Path] = set()

    def pick(key_fn, n: int) -> None:
        seen: list = []
        for dt, path in ordered:
            key = key_fn(dt)
            if key in seen:
                continue
            if len(seen) >= n:
                break
            seen.append(key)
            keep.add(path)

    pick(lambda d: d.date(), daily)
    pick(lambda d: tuple(d.isocalendar())[:2], weekly)
    pick(lambda d: (d.year, d.month), monthly)
    return keep


def _integrity_ok(db_file: Path) -> bool:
    conn = sqlite3.connect(str(db_file))
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def make_backup(db_path: Path, dest_dir: Path, now: datetime | None = None) -> Path:
    db_path = Path(db_path)
    if not db_path.is_file() or db_path.stat().st_size == 0:
        raise BackupError(f"No hay base de datos (o esta vacia) en {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    final = dest_dir / f"{PREFIX}{now.strftime(_STAMP)}{SUFFIX}"

    with tempfile.TemporaryDirectory(dir=dest_dir, prefix=".tmp_backup_") as tmp:
        copy = Path(tmp) / "copy.db"
        src = sqlite3.connect(str(db_path), timeout=120)
        dst = sqlite3.connect(str(copy))
        try:
            src.backup(dst)          # copia consistente aunque haya escrituras en curso
        finally:
            dst.close()
            src.close()
        if not _integrity_ok(copy):
            raise BackupError("La copia NO supera PRAGMA integrity_check -- se descarta.")
        partial = final.with_name(final.name + ".tmp")
        with open(copy, "rb") as f_in, gzip.open(partial, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
        partial.replace(final)       # atomico: o esta entera o no existe
    return final


def verify_backup(backup: Path) -> bool:
    """Descomprime en un temporal y pasa integrity_check."""
    with tempfile.TemporaryDirectory(dir=backup.parent, prefix=".tmp_verify_") as tmp:
        copy = Path(tmp) / "copy.db"
        with gzip.open(backup, "rb") as f_in, open(copy, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
        return _integrity_ok(copy)


def prune(dest_dir: Path, daily: int, weekly: int, monthly: int) -> list[Path]:
    entries = list_backups(dest_dir)
    keep = select_to_keep(entries, daily, weekly, monthly)
    removed = []
    for _dt, path in entries:
        if path not in keep:
            path.unlink()
            removed.append(path)
    return removed


def _mb(path: Path) -> str:
    return f"{path.stat().st_size / 1024 / 1024:.0f} MB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copia de seguridad de basketball.db")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--keep-daily", type=int, default=7)
    ap.add_argument("--keep-weekly", type=int, default=4)
    ap.add_argument("--keep-monthly", type=int, default=3)
    ap.add_argument("--no-prune", action="store_true", help="no borrar copias antiguas")
    ap.add_argument("--list", action="store_true", help="listar copias y salir")
    ap.add_argument("--verify", action="store_true", help="verificar la copia mas reciente y salir")
    args = ap.parse_args(argv)

    if args.list:
        for dt, path in list_backups(args.dest):
            print(f"{dt:%Y-%m-%d %H:%M}  {_mb(path):>8}  {path}")
        return 0

    if args.verify:
        entries = list_backups(args.dest)
        if not entries:
            print(f"No hay copias en {args.dest}", flush=True)
            return 1
        newest = entries[0][1]
        ok = verify_backup(newest)
        print(f"{'OK' if ok else 'FALLO'}: {newest}", flush=True)
        return 0 if ok else 1

    try:
        final = make_backup(args.db, args.dest)
    except (BackupError, sqlite3.Error, OSError) as exc:
        print(f"ERROR en la copia de seguridad: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Copia creada: {final} ({_mb(final)})", flush=True)

    if not args.no_prune:
        removed = prune(args.dest, args.keep_daily, args.keep_weekly, args.keep_monthly)
        for p in removed:
            print(f"Borrada copia antigua: {p.name}", flush=True)
        print(f"Copias conservadas: {len(list_backups(args.dest))}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
