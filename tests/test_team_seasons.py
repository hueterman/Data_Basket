import sqlite3

from src.pipeline.repository import team_seasons


def test_team_seasons_distingue_ids_por_temporada():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE games(source TEXT, game_id TEXT, season TEXT, home_team_id TEXT, away_team_id TEXT)")
    c.executemany("INSERT INTO games VALUES('feb', ?, ?, ?, ?)", [
        ("1", "2022", "888968", "x"), ("2", "2022", "x", "888968"),
        ("3", "2023", "922723", "x"),
    ])
    c.execute("INSERT INTO games VALUES('acb','9','2023-24','a','b')")
    r = team_seasons(c)
    assert r[("feb", "888968")] == ["2022"]
    assert r[("feb", "922723")] == ["2023"]
    assert r[("feb", "x")] == ["2022", "2023"]
    assert r[("acb", "a")] == ["2023-24"]
