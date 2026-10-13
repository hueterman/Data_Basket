"""Reintentos HTTP de AcbSource (src/scraper/acb.py): sin red, con una sesion falsa."""
import pytest
import requests

from src.scraper import acb as acb_mod
from src.scraper.acb import AcbSource, AcbSourceError


class FakeResponse:
    def __init__(self, status=200, text="<html></html>", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class FakeSession:
    """Devuelve (o lanza) lo que le digan, en orden."""
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.headers = {}

    def get(self, url, timeout=None):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def source(monkeypatch):
    esperas = []
    monkeypatch.setattr(acb_mod.time, "sleep", lambda s: esperas.append(s))
    src = AcbSource()
    src.esperas = esperas
    return src


def test_502_transitorio_se_recupera(source):
    source._session = FakeSession([FakeResponse(502), FakeResponse(502), FakeResponse(200, "<p>ok</p>")])
    soup = source._get_soup("http://x", context="prueba")
    assert soup.p.text == "ok"
    assert source._session.calls == 3
    assert source.esperas == [2.0, 4.0]          # espera creciente


def test_si_se_agotan_los_reintentos_falla_con_error_claro(source):
    source._session = FakeSession([FakeResponse(502)] * 4)
    with pytest.raises(AcbSourceError, match="Fallo pidiendo prueba"):
        source._get_soup("http://x", context="prueba")
    assert source._session.calls == 4            # 1 intento + 3 reintentos
    assert source.esperas == [2.0, 4.0, 8.0]


def test_404_no_se_reintenta(source):
    source._session = FakeSession([FakeResponse(404)])
    with pytest.raises(AcbSourceError):
        source._get_soup("http://x", context="prueba")
    assert source._session.calls == 1 and source.esperas == []


def test_errores_de_conexion_y_timeout_se_reintentan(source):
    source._session = FakeSession([requests.ConnectionError("caida"), requests.Timeout("lento"),
                                   FakeResponse(200, "<p>ok</p>")])
    assert source._get_soup("http://x", context="prueba").p.text == "ok"
    assert source._session.calls == 3


def test_retry_after_del_servidor_se_respeta_con_tope(source):
    source._session = FakeSession([FakeResponse(429, headers={"Retry-After": "7"}),
                                   FakeResponse(503, headers={"Retry-After": "9999"}),
                                   FakeResponse(200)])
    source._get_soup("http://x", context="prueba")
    assert source.esperas == [7.0, 30.0]         # 7 s tal cual; 9999 s -> tope de 30 s


def test_boxscore_tambien_reintenta(source):
    # Un 502 seguido de una pagina con una tabla que no es la de jugadores: demuestra
    # que reintento y luego llego al parseo (que es quien lanza el AcbSourceError).
    pagina = "<table><tr><th>Otra</th></tr><tr><td>1</td></tr></table>"
    source._session = FakeSession([FakeResponse(502), FakeResponse(200, pagina)])
    with pytest.raises(AcbSourceError):
        source.get_boxscore("barca-vs-real-madrid-1")
    assert source._session.calls == 2


def test_season_con_fin_incoherente_se_rechaza():
    from src.scraper import acb
    assert acb._edition_id_from_season("2011-12") == 76
    assert acb._edition_id_from_season("1999-00") == 64
    with pytest.raises(acb.AcbSourceError):
        acb._edition_id_from_season("2011-10")
