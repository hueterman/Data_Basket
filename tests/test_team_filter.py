import sqlite3

from src.pipeline import clubs


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE games(source TEXT, game_id TEXT, season TEXT, home_team_id TEXT, away_team_id TEXT)")
    c.execute("CREATE TABLE teams(source TEXT, team_id TEXT, name TEXT)")
    c.executemany("INSERT INTO games VALUES('feb', ?, ?, ?, ?)", [
        ("1", "2021", "a21", "x"), ("2", "2022", "a22", "x"), ("3", "2022", "y", "a22"),
    ])
    c.execute("INSERT INTO games VALUES('acb','9','2023-24','a21','z')")
    c.executemany("INSERT INTO teams VALUES('feb', ?, ?)", [
        ("a21", "ANTIGUO PATROCINADOR"), ("a22", "NUEVO PATROCINADOR"), ("x", "Equipo X"), ("y", "Equipo Y")])
    clubs.ensure_table(c)
    c.executemany("INSERT INTO team_club(source, team_id, club_id) VALUES('feb', ?, 'club:a')", [("a21",), ("a22",)])
    return c


def test_team_options_une_ids_del_mismo_club_y_usa_el_nombre_mas_reciente():
    opts = clubs.team_options(_db(), "feb")
    assert ("club:a", "NUEVO PATROCINADOR") in opts
    assert [k for k, _ in opts].count("club:a") == 1
    assert {k for k, _ in opts} == {"club:a", "x", "y"}


def test_team_options_solo_de_la_fuente_pedida():
    assert clubs.team_options(_db(), "acb") == [("a21", "a21"), ("z", "z")]


def test_team_ids_for_devuelve_todos_los_ids_del_club():
    c = _db()
    assert clubs.team_ids_for(c, "feb", "club:a") == ["a21", "a22"]
    assert clubs.team_ids_for(c, "feb", "x") == ["x"]
    assert clubs.team_ids_for(c, "feb", "club:no-existe") == []
