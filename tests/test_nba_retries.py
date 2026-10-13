"""Reintentos / enfriamiento de peticiones por partido de NBA (sin red)."""
import pytest

from src.scraper import nba as nba_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    nba_mod._fail_streak = 0
    sleeps = []
    monkeypatch.setattr(nba_mod.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _flaky(fail_times, value="ok"):
    state = {"n": 0}

    def call():
        state["n"] += 1
        if state["n"] <= fail_times:
            raise TimeoutError("Read timed out")
        return value
    call.state = state
    return call


def test_reintenta_con_espera_creciente_y_acaba_bien(_reset):
    f = _flaky(2)
    assert nba_mod._request_with_retry(f, "x") == "ok"
    assert f.state["n"] == 3
    assert _reset == [5.0, 10.0]
    assert nba_mod._fail_streak == 0


def test_agota_reintentos_y_relanza(_reset):
    f = _flaky(99)
    with pytest.raises(TimeoutError):
        nba_mod._request_with_retry(f, "x")
    assert f.state["n"] == 3 and nba_mod._fail_streak == 1


def test_en_racha_no_reintenta(_reset):
    nba_mod._fail_streak = 2
    f = _flaky(99)
    with pytest.raises(TimeoutError):
        nba_mod._request_with_retry(f, "x")
    assert f.state["n"] == 1 and _reset == [] and nba_mod._fail_streak == 3


def test_pausa_larga_al_llegar_al_limite_y_vuelve_a_reintentar(_reset):
    nba_mod._fail_streak = nba_mod._FAIL_STREAK_LIMIT
    f = _flaky(1)
    assert nba_mod._request_with_retry(f, "x") == "ok"
    assert _reset[0] == nba_mod._COOLDOWN_SECONDS      # primero la pausa larga
    assert f.state["n"] == 2                            # y despues SI reintenta


def test_get_boxscore_envuelve_el_error_final(monkeypatch, _reset):
    def boom(*a, **k):
        raise TimeoutError("Read timed out")
    monkeypatch.setattr(nba_mod.boxscoretraditionalv3, "BoxScoreTraditionalV3", boom)
    with pytest.raises(nba_mod.NbaSourceError):
        nba_mod.NbaSource("2018-19").get_boxscore("0021800917")
