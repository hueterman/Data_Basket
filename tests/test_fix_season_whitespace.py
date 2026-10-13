import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fix_season_whitespace as fx  # noqa: E402


def _db(tmp_path):
    db = tmp_path / "b.db"
    c = sqlite3.connect(str(db))
    for t in fx.TABLES:
        c.execute(f"CREATE TABLE {t}(source TEXT, season TEXT)")
        c.executemany(f"INSERT INTO {t} VALUES('acb', ?)", [(" 2014-15",), ("2016-17",)])
    c.commit()
    return db, c


def test_detecta_y_corrige_bd(tmp_path):
    db, c = _db(tmp_path)
    assert set(fx.find_dirty(c)) == set(fx.TABLES)
    assert fx.fix_db(c) == {"games": 1, "ingest_log": 1}
    assert fx.find_dirty(c) == {}


def test_renombra_carpetas_y_no_pisa_existentes(tmp_path):
    raw = tmp_path / "raw"
    (raw / "acb" / " 2014-15").mkdir(parents=True)
    (raw / "acb" / " 2015-16").mkdir()
    (raw / "acb" / "2015-16").mkdir()
    dirs = fx.find_dirty_dirs(raw)
    assert len(dirs) == 2
    log = fx.fix_dirs(dirs)
    assert (raw / "acb" / "2014-15").is_dir()
    assert (raw / "acb" / " 2015-16").is_dir()          # no se pisa
    assert any("OMITIDA" in x for x in log)


def test_main_simulacro_no_cambia_nada(tmp_path):
    db, c = _db(tmp_path)
    c.close()
    assert fx.main(["--db", str(db), "--raw", str(tmp_path / "raw")]) == 0
    c = sqlite3.connect(str(db))
    assert fx.find_dirty(c)
