"""Tests de src/perf.py: puros (sin Flask, sin modelos, sin red)."""
import sqlite3

import pytest

from src import perf


@pytest.fixture(autouse=True)
def _limpia_env(monkeypatch):
    for var in ("DATA_BASKET_PROFILE", "DB_CACHE_KB", "DB_MMAP_MB", "DB_SYNCHRONOUS",
                "WAITRESS_THREADS", "SHOT_CACHE", "MODEL_NTHREAD"):
        monkeypatch.delenv(var, raising=False)


def test_perfil_default_no_cambia_nada(monkeypatch):
    monkeypatch.setenv("DATA_BASKET_PROFILE", "default")
    cfg = perf.settings()
    assert cfg["db_cache_kb"] is None and cfg["db_mmap_mb"] == 0
    assert cfg["db_synchronous"] is None and cfg["shot_cache"] is False


def test_perfil_pi(monkeypatch):
    monkeypatch.setenv("DATA_BASKET_PROFILE", "pi")
    cfg = perf.settings()
    assert cfg["db_synchronous"] == "NORMAL" and cfg["shot_cache"] is True
    assert cfg["db_cache_kb"] == 16384


def test_auto_sin_pi_es_default(monkeypatch):
    monkeypatch.setattr(perf, "_is_raspberry_pi", lambda: False)
    assert perf.active_profile() == "default"
    monkeypatch.setattr(perf, "_is_raspberry_pi", lambda: True)
    assert perf.active_profile() == "pi"


def test_perfil_desconocido_cae_en_default(monkeypatch):
    monkeypatch.setenv("DATA_BASKET_PROFILE", "inventado")
    assert perf.active_profile() == "default"


def test_variables_sobrescriben_el_perfil(monkeypatch):
    monkeypatch.setenv("DATA_BASKET_PROFILE", "pi")
    monkeypatch.setenv("WAITRESS_THREADS", "8")
    monkeypatch.setenv("SHOT_CACHE", "0")
    monkeypatch.setenv("DB_SYNCHRONOUS", "full")
    cfg = perf.settings()
    assert cfg["waitress_threads"] == 8 and cfg["shot_cache"] is False
    assert cfg["db_synchronous"] == "FULL"


def test_valores_invalidos_se_ignoran(monkeypatch):
    monkeypatch.setenv("DATA_BASKET_PROFILE", "pi")
    monkeypatch.setenv("WAITRESS_THREADS", "abc")
    monkeypatch.setenv("DB_SYNCHRONOUS", "peligroso; DROP TABLE x")
    cfg = perf.settings()
    assert cfg["waitress_threads"] == 4 and cfg["db_synchronous"] == "NORMAL"


def test_pragmas_se_aplican(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_BASKET_PROFILE", "pi")
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    perf.apply_sqlite_pragmas(conn)
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1          # NORMAL
    assert conn.execute("PRAGMA cache_size").fetchone()[0] == -16384
    conn.close()


def test_warmup_activo_solo_en_pi(monkeypatch):
    monkeypatch.setenv("DATA_BASKET_PROFILE", "default")
    assert perf.settings()["warmup"] is False
    monkeypatch.setenv("DATA_BASKET_PROFILE", "pi")
    assert perf.settings()["warmup"] is True
    monkeypatch.setenv("WARMUP", "0")
    assert perf.settings()["warmup"] is False


def test_warm_models_llama_a_cada_modelo_y_tolera_fallos(monkeypatch, tmp_path):
    import sys
    import types

    llamadas = []

    def shot(**kw):
        llamadas.append(("tiro", kw["model_name"]))

    def game(conn, **kw):
        llamadas.append(("partido", kw["model_name"], kw["margin_model_name"], kw["home_team_id"]))
        if kw["home_team_id"] == "t1":  # el primer par "no tiene partidos"
            raise RuntimeError("sin partidos")

    fake = types.SimpleNamespace(
        available_shot_models=lambda: ["logreg", "xgboost"],
        available_models=lambda: ["logreg", "xgboost"],
        available_margin_models=lambda: ["ridge", "xgboost"],
        shot_make_probability=shot,
        predict_game=game,
    )
    monkeypatch.setitem(sys.modules, "src.predict", fake)

    def open_conn():
        c = sqlite3.connect(str(tmp_path / "w.db"))
        c.execute("CREATE TABLE IF NOT EXISTS teams(source TEXT, team_id TEXT)")
        if not c.execute("SELECT 1 FROM teams").fetchone():
            c.executemany("INSERT INTO teams VALUES (?,?)",
                          [("nba", "t1"), ("nba", "t2"), ("nba", "t3"), ("nba", "t4")])
            c.commit()
        return c

    from src import warmup
    res = warmup.warm_models(open_conn, log=lambda *_: None)

    assert ("tiro", "logreg") in llamadas and ("tiro", "xgboost") in llamadas
    # el par t1-t2 falla, el calentamiento sigue con t3-t4 y recorre todas las combinaciones
    partidos_ok = [l for l in llamadas if l[0] == "partido" and l[3] == "t3"]
    assert {(l[1], l[2]) for l in partidos_ok} == {("logreg", "ridge"), ("xgboost", "xgboost")}
    assert any(not ok for _, ok, _ in res)  # el fallo quedo registrado, sin excepcion
