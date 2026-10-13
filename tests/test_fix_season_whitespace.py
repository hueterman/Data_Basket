import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fix_season_whitespace as fx  # noqa: E402


def test_canonical():
    assert fx.canonical(" 2015-16") == "2015-16"
    assert fx.canonical(" 2015-14") == "2015-16"
    assert fx.canonical("2011-10") == "2011-12"
    assert fx.canonical("1999-99") == "1999-00"
    assert fx.canonical("2026-27") == "2026-27"
    assert fx.canonical("JTB23") == "JTB23"          # codigos de otras fuentes: intactos
    assert fx.canonical("2026") == "2026"


def _db(tmp_path):
    db = tmp_path / "b.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE games(source TEXT, game_id TEXT, season TEXT, date TEXT)")
    c.execute("CREATE TABLE ingest_log(source TEXT, season TEXT)")
    rows = [("acb", "a", " 2014-15", "2014-10-05"),      # solo espacio
            ("acb", "b", " 2015-14", "2016-03-01"),      # fin incoherente, fecha de la 2015-16 (ene-jun)
            ("acb", "c", " 2012-11", "2013-05-01"),
            ("acb", "d", "2016-15", "2018-01-01"),       # fechas NO casan con 2016 -> no se toca
            ("acb", "e", "2016-17", "2016-10-01")]       # correcta
    c.executemany("INSERT INTO games VALUES(?,?,?,?)", rows)
    c.executemany("INSERT INTO ingest_log VALUES('acb', ?)", [(" 2011-10",), ("2016-17",)])
    c.commit()
    return db, c


def test_detecta_y_corrige_con_confirmacion_por_fechas(tmp_path):
    db, c = _db(tmp_path)
    assert fx.games_confirmed(c, "acb", " 2015-14")
    assert not fx.games_confirmed(c, "acb", "2016-15")
    changed = fx.fix_db(c)
    assert changed == {"games": 3, "ingest_log": 1}
    seasons = dict(c.execute("SELECT game_id, season FROM games"))
    assert seasons == {"a": "2014-15", "b": "2015-16", "c": "2012-13", "d": "2016-15", "e": "2016-17"}
    assert c.execute("SELECT season FROM ingest_log ORDER BY 1").fetchall() == [("2011-12",), ("2016-17",)]


def test_renombra_carpetas_y_no_pisa_existentes(tmp_path):
    raw = tmp_path / "raw"
    (raw / "acb" / " 2014-15").mkdir(parents=True)
    (raw / "acb" / " 2015-14").mkdir()
    (raw / "acb" / " 2016-15").mkdir()
    (raw / "acb" / "2016-17").mkdir()
    (raw / "acb" / " 2016-17").mkdir()
    log = fx.fix_dirs(fx.find_dirty_dirs(raw))
    assert (raw / "acb" / "2014-15").is_dir() and (raw / "acb" / "2015-16").is_dir()
    assert any("OMITIDA" in x for x in log)
    assert (raw / "acb" / " 2016-17").is_dir()           # no se pisa


def test_main_simulacro_no_cambia_nada(tmp_path):
    db, c = _db(tmp_path)
    c.close()
    assert fx.main(["--db", str(db), "--raw", str(tmp_path / "raw")]) == 0
    c = sqlite3.connect(str(db))
    assert fx.find_dirty(c)
