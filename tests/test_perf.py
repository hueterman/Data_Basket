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
