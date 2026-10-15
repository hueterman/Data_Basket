import sqlite3

from src.pipeline import raw_cache
from src.pipeline.repository import replace_shots
from src.pipeline.schema import init_db
from src.scraper.base import Shot
from src.scraper.euroleague import period_from_minute


def test_period_from_minute_cuartos_y_prorrogas():
    assert [period_from_minute(m) for m in (1, 10, 11, 20, 21, 30, 31, 40)] == [1, 1, 2, 2, 3, 3, 4, 4]
    assert [period_from_minute(m) for m in (41, 45, 46, 50, 51)] == [5, 5, 6, 6, 7]
    assert period_from_minute("7") == 1
    assert period_from_minute(None) is None
    assert period_from_minute("") is None
    assert period_from_minute(0) is None


def _db_con():
    c = sqlite3.connect(":memory:")
    init_db(c)
    return c


def test_replace_shots_guarda_periodo():
    c = _db_con()
    replace_shots(c, "nba", "g1", [Shot("g1", "p", "t", 1.0, 2.0, True, "2", 3), Shot("g1", "p", "t", 1.0, 2.0, False, "3")])
    assert c.execute("SELECT period FROM shots ORDER BY shot_id").fetchall() == [(3,), (None,)]


def test_init_db_migra_una_bd_antigua_sin_columna_period():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE shots (shot_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, game_id TEXT NOT NULL,"
              " player_id TEXT NOT NULL, team_id TEXT NOT NULL, loc_x REAL NOT NULL, loc_y REAL NOT NULL,"
              " made INTEGER NOT NULL, shot_type TEXT)")
    c.execute("INSERT INTO shots(source, game_id, player_id, team_id, loc_x, loc_y, made) VALUES('nba','g','p','t',0,0,1)")
    init_db(c)
    init_db(c)  # idempotente
    assert c.execute("SELECT period FROM shots").fetchall() == [(None,)]


def test_cache_antigua_sin_periodo_sigue_cargando(tmp_path):
    p = raw_cache.shots_path("nba", "2019-20", "g1", tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text('[{"game_id":"g1","player_id":"1","team_id":"2","loc_x":0.0,"loc_y":1.0,"made":true,"shot_type":"2"}]')
    (shot,) = raw_cache.load_shots("nba", "2019-20", "g1", tmp_path)
    assert shot.period is None
    raw_cache.save_shots("nba", "2019-20", "g1", [Shot("g1", "1", "2", 0.0, 1.0, True, "2", 4)], tmp_path)
    assert raw_cache.load_shots("nba", "2019-20", "g1", tmp_path)[0].period == 4
