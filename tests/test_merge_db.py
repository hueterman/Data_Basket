import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import merge_db as mg  # noqa: E402

SCHEMA = """
CREATE TABLE games(source TEXT, game_id TEXT, season TEXT, round INTEGER, date TEXT,
  home_team_id TEXT, away_team_id TEXT, home_score INTEGER, away_score INTEGER, played INTEGER,
  PRIMARY KEY(source, game_id));
CREATE TABLE player_stats(source TEXT, game_id TEXT, player_id TEXT, player_name TEXT, team_id TEXT,
  minutes REAL, points INTEGER, rebounds INTEGER, assists INTEGER, val INTEGER, plus_minus INTEGER,
  PRIMARY KEY(source, game_id, player_id));
CREATE TABLE team_stats(source TEXT, game_id TEXT, team_id TEXT, t2_made INTEGER, val INTEGER,
  PRIMARY KEY(source, game_id, team_id));
CREATE TABLE shots(shot_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, game_id TEXT,
  player_id TEXT, team_id TEXT, loc_x REAL, loc_y REAL, made INTEGER, shot_type TEXT);
CREATE TABLE teams(source TEXT, team_id TEXT, name TEXT, PRIMARY KEY(source, team_id));
CREATE TABLE players(source TEXT, player_id TEXT, name TEXT, team_id TEXT, PRIMARY KEY(source, player_id));
CREATE TABLE coaches(source TEXT, coach_id TEXT, name TEXT, team_id TEXT, PRIMARY KEY(source, coach_id));
CREATE TABLE sources(source TEXT PRIMARY KEY, trainable INTEGER);
"""


def _mk(path, games):
    c = sqlite3.connect(str(path))
    c.executescript(SCHEMA)
    for src, gid, season, played in games:
        c.execute("INSERT INTO games VALUES(?,?,?,1,'2020-01-01','a','b',?,?,?)",
                  (src, gid, season, 80 if played else None, 70 if played else None, played))
        c.execute("INSERT INTO player_stats VALUES(?,?,?,?,?,10,5,1,1,3,0)", (src, gid, "p1", "P", "a"))
        c.execute("INSERT INTO team_stats VALUES(?,?,?,5,10)", (src, gid, "a"))
        c.execute("INSERT INTO shots(source,game_id,player_id,team_id,loc_x,loc_y,made,shot_type) "
                  "VALUES(?,?,?,?,0.1,0.2,1,'2')", (src, gid, "p1", "a"))
    c.execute("INSERT INTO teams VALUES('acb','a','Equipo A')")
    c.execute("INSERT INTO sources VALUES('acb',1)")
    c.commit()
    return c


def _setup(tmp_path):
    main = _mk(tmp_path / "main.db", [("acb", "g1", "2023-24", 1), ("acb", "g3", "2023-24", 0)])
    other = _mk(tmp_path / "other.db", [("acb", "g1", "2023-24", 1), ("acb", "g2", " 2014-15", 1),
                                        ("acb", "g3", "2023-24", 1)])
    other.execute("UPDATE player_stats SET points=99 WHERE game_id='g1'")   # no debe pisar g1
    other.commit()
    other.close()
    main.execute("ATTACH DATABASE ? AS other", (str(tmp_path / "other.db"),))
    return main


def test_plan_distingue_nuevos_y_sustituidos(tmp_path):
    c = _setup(tmp_path)
    new, upd = mg.plan(c)
    assert new == [("acb", "g2")] and upd == [("acb", "g3")]


def test_merge_anade_sin_duplicar_ni_pisar(tmp_path):
    c = _setup(tmp_path)
    res = mg.merge(c)
    c.commit()
    assert res["games_nuevos"] == 1 and res["games_sustituidos"] == 1
    q = lambda s: c.execute(s).fetchall()
    assert q("SELECT COUNT(*) FROM games")[0][0] == 3
    assert q("SELECT season FROM games WHERE game_id='g2'") == [("2014-15",)]      # TRIM
    assert q("SELECT points FROM player_stats WHERE game_id='g1'") == [(5,)]        # g1 intacto
    assert q("SELECT played FROM games WHERE game_id='g3'") == [(1,)]               # g3 sustituido
    assert q("SELECT COUNT(*) FROM shots")[0][0] == 3                              # 1 por partido, sin duplicar
    assert q("SELECT COUNT(*) FROM player_stats")[0][0] == 3
    assert q("SELECT COUNT(*) FROM teams")[0][0] == 1
    # idempotente: segunda pasada no cambia nada
    res2 = mg.merge(c)
    assert res2["games_nuevos"] == 0 and res2["games_sustituidos"] == 0
    assert q("SELECT COUNT(*) FROM shots")[0][0] == 3
