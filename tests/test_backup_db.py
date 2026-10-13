"""Tests de scripts/backup_db.py: solo libreria estandar, sin la BD real."""
import gzip
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backup_db as bk  # noqa: E402


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def _entries(*stamps):
    return [(_dt(s), Path(f"{bk.PREFIX}{_dt(s):%Y-%m-%d_%H%M}{bk.SUFFIX}")) for s in stamps]


def test_nombre_de_copia_se_parsea_y_ajenos_se_ignoran():
    assert bk.parse_backup_name(Path("basketball_2026-10-13_0630.db.gz")) == _dt("2026-10-13 06:30")
    assert bk.parse_backup_name(Path("otro.txt")) is None
    assert bk.parse_backup_name(Path("basketball_basura.db.gz")) is None


def test_retencion_una_por_dia_semana_y_mes():
    # 10 dias seguidos (dos copias el 13), mas copias de semanas y meses anteriores
    e = _entries(
        "2026-10-13 06:30", "2026-10-13 18:00",   # mismo dia: solo la mas reciente cuenta como "diaria"
        "2026-10-12 06:30", "2026-10-11 06:30", "2026-10-10 06:30", "2026-10-09 06:30",
        "2026-10-08 06:30", "2026-10-07 06:30", "2026-10-06 06:30", "2026-10-05 06:30",
        "2026-09-20 06:30", "2026-08-20 06:30", "2026-07-20 06:30", "2026-01-05 06:30",
    )
    keep = bk.select_to_keep(e, daily=7, weekly=4, monthly=3)
    nombres = {p.name for p in keep}
    assert "basketball_2026-10-13_1800.db.gz" in nombres          # la mas reciente del dia 13
    assert "basketball_2026-10-13_0630.db.gz" not in nombres      # la otra del mismo dia sobra
    assert "basketball_2026-10-07_0630.db.gz" in nombres          # 7.o dia con copias
    assert "basketball_2026-08-20_0630.db.gz" in nombres          # mensual
    assert "basketball_2026-01-05_0630.db.gz" not in nombres      # demasiado antigua


def test_retencion_nunca_deja_sin_copias():
    e = _entries("2026-10-13 06:30")
    assert len(bk.select_to_keep(e)) == 1
    assert bk.select_to_keep([]) == set()


def _crear_bd(path, filas=50):
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, x TEXT)")
    conn.executemany("INSERT INTO t(x) VALUES (?)", [(f"fila{i}",) for i in range(filas)])
    conn.commit()
    return conn


def test_copia_es_consistente_comprimida_y_verificable(tmp_path):
    db = tmp_path / "basketball.db"
    conn = _crear_bd(db)             # conexion abierta + WAL: caso real de la app
    dest = tmp_path / "backups"
    final = bk.make_backup(db, dest, now=_dt("2026-10-13 06:30"))
    conn.close()

    assert final.name == "basketball_2026-10-13_0630.db.gz"
    assert not list(dest.glob("*.tmp")) and not list(dest.glob(".tmp_*"))   # sin restos
    restaurada = tmp_path / "restaurada.db"
    restaurada.write_bytes(gzip.decompress(final.read_bytes()))
    c = sqlite3.connect(str(restaurada))
    assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 50
    c.close()
    assert bk.verify_backup(final) is True


def test_bd_inexistente_o_vacia_da_error_claro(tmp_path):
    with pytest.raises(bk.BackupError):
        bk.make_backup(tmp_path / "no_existe.db", tmp_path / "b")
    vacia = tmp_path / "vacia.db"
    vacia.write_bytes(b"")
    with pytest.raises(bk.BackupError):
        bk.make_backup(vacia, tmp_path / "b")


def test_main_copia_y_poda(tmp_path, capsys):
    db = tmp_path / "basketball.db"
    _crear_bd(db).close()
    dest = tmp_path / "backups"
    dest.mkdir()
    for s in ("2026-01-01_0630", "2026-01-02_0630"):          # muy antiguas: deben podarse
        (dest / f"basketball_{s}.db.gz").write_bytes(b"x")
    assert bk.main(["--db", str(db), "--dest", str(dest), "--keep-daily", "1",
                    "--keep-weekly", "1", "--keep-monthly", "2"]) == 0
    nombres = sorted(p.name for p in dest.glob("*.db.gz"))
    assert len(nombres) == 2                                   # la nueva (octubre) + la mas reciente de enero (2 meses con copias)
    assert "basketball_2026-01-02_0630.db.gz" in nombres
    assert "basketball_2026-01-01_0630.db.gz" not in nombres
    assert bk.main(["--dest", str(dest), "--list"]) == 0
    assert bk.main(["--dest", str(dest), "--verify"]) == 0
