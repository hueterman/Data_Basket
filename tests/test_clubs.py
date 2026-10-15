import sqlite3

import pandas as pd
import pytest

from src.pipeline import clubs


def test_normalize_name():
    assert clubs.normalize_name("ACTEL FORÇA LLEIDA") == "ACTEL FORCA LLEIDA"
    assert clubs.normalize_name("  Cloud.Gal   Ourense ") == "CLOUD GAL OURENSE"
    assert clubs.normalize_name(None) == ""


TEAMS = [("feb", "1", "Alimerka Oviedo"), ("feb", "2", "ALIMERKA OVIEDO"), ("feb", "3", "Alimerka Oviedo"),
         ("feb", "9", "Solo Una Vez"), ("acb", "a", "Real Madrid")]
SEASONS = {("feb", "1"): ["2022"], ("feb", "2"): [" 2023"], ("feb", "3"): ["2024"],
           ("feb", "9"): ["2022"], ("acb", "a"): ["2023-24"]}


def test_une_mismo_nombre_en_temporadas_distintas_y_deja_singletons_sin_fila():
    m = clubs.compute_mapping(TEAMS, SEASONS)
    assert m[("feb", "1")] == m[("feb", "2")] == m[("feb", "3")] == "club:alimerka-oviedo"
    assert ("feb", "9") not in m and ("acb", "a") not in m       # un solo id: no se toca


def test_mismo_nombre_en_la_misma_temporada_son_equipos_distintos():
    teams = [("feb", "1", "Filial X"), ("feb", "2", "Filial X"), ("feb", "3", "Filial X")]
    seasons = {("feb", "1"): ["2022"], ("feb", "2"): ["2022"], ("feb", "3"): ["2023"]}
    m = clubs.compute_mapping(teams, seasons)
    assert m[("feb", "1")] == m[("feb", "3")] == "club:filial-x"   # 1 y 3 no coinciden
    assert ("feb", "2") not in m                                    # 2 coincide con 1: aparte


def test_alias_une_cambio_de_nombre(tmp_path):
    csv_path = tmp_path / "a.csv"
    csv_path.write_text("# comentario\nsource,name,same_as\nfeb,Unicaja Banco Oviedo,Alimerka Oviedo\n",
                        encoding="utf-8")
    teams = TEAMS + [("feb", "7", "UNICAJA BANCO OVIEDO")]
    seasons = {**SEASONS, ("feb", "7"): ["2021"]}
    m = clubs.compute_mapping(teams, seasons, clubs.load_aliases(csv_path))
    assert m[("feb", "7")] == "club:alimerka-oviedo"
    assert clubs.load_aliases(tmp_path / "no_existe.csv") == {}


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE teams(source TEXT, team_id TEXT, name TEXT)")
    c.execute("CREATE TABLE games(source TEXT, game_id TEXT, season TEXT, home_team_id TEXT, away_team_id TEXT)")
    c.executemany("INSERT INTO teams VALUES(?,?,?)", TEAMS)
    for i, (tid, ss) in enumerate([("1", "2022"), ("2", "2023"), ("3", "2024"), ("9", "2022")]):
        c.execute("INSERT INTO games VALUES('feb', ?, ?, ?, 'zz')", (str(i), ss, tid))
    return c


def test_rebuild_load_y_apply():
    c = _db()
    assert clubs.load_map(c) == {}                                   # sin tabla: identidad
    df = pd.DataFrame({"source": ["feb", "feb", "acb"], "team_id": ["2", "9", "a"], "opp_id": ["zz"] * 3})
    assert clubs.apply_club_ids(c, df).equals(df)
    assert clubs.rebuild(c, ["feb"]) == 3
    out = clubs.apply_club_ids(c, df)
    assert list(out["team_id"]) == ["club:alimerka-oviedo", "9", "a"]
    assert list(df["team_id"]) == ["2", "9", "a"]                    # no modifica el original
    assert clubs.club_of(c, "feb", "3") == "club:alimerka-oviedo"
    assert clubs.club_of(c, "feb", "9") == "9"
    assert clubs.rebuild(c, ["feb"]) == 3                            # idempotente
    assert c.execute("SELECT COUNT(*) FROM team_club").fetchone()[0] == 3
