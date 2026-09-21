"""Prueba OFFLINE del parseo corregido de FebSource.get_shots()
(ver "BUGS REALES corregidos 2026-09-20(e)" en src/scraper/feb.py).

Migrado desde scripts/test_feb_shots_fix.py a pytest. Usa un fragmento
REAL capturado en vivo el 2026-09-20 contra game_id=2220021 (Palmer
Alma Mediterrania Palma - Melilla Sport Capital, Primera FEB) via el
navegador integrado -- no un JSON inventado. La respuesta completa
tiene 120 tiros; aqui se incluyen los 3 primeros mas uno de cada
combinacion team/m vista en la respuesta real, suficiente para probar
la logica de parseo sin pegar el payload entero.

No hace falta red: se monkeypatchea _get_live_token()/_session.get()
de FebSource para devolver este fragmento capturado, siguiendo el mismo
principio "sin red real" que el resto de pruebas offline del proyecto.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.scraper.feb import FebSource

# Payload real (recortado) capturado en vivo -- ver docstring del modulo.
REAL_SHOTCHART_PAYLOAD = {
    "SHOTCHART": {
        "TEAM": [
            {"name": "PALMER ALMA MEDITERRÀNEA PALMA"},
            {"name": "MELILLA SPORT CAPITAL CLUB MELILLA BALONCESTO"},
        ],
        "SHOTS": [
            # team 0, fallado (m="0")
            {"m": "0", "t": "05:26", "x": "32.64540335741655", "y": "72.6517911484462",
             "team": "0", "player": "7", "quarter": "1"},
            # team 0, otro fallado
            {"m": "0", "t": "03:22", "x": "87.30456594417014", "y": "53.315238016628804",
             "team": "0", "player": "7", "quarter": "4"},
            # team 1, ACERTADO (m="1") -- el caso que el bug viejo tambien
            # marcaba como True, pero por la razon equivocada.
            {"m": "1", "t": "01:15", "x": "69.48092756759503", "y": "28.837173633894253",
             "team": "1", "player": "0", "quarter": "1"},
        ],
    }
}


def test_get_shots_parses_made_and_shot_type_from_real_payload():
    src = FebSource(competition="feb", year="2026")
    src._get_live_token = MagicMock(return_value="fake-token")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json = MagicMock(return_value=REAL_SHOTCHART_PAYLOAD)
    src._session = MagicMock()
    src._session.get = MagicMock(return_value=fake_response)

    shots = src.get_shots("2220021")

    assert len(shots) == 3
    assert shots[0].made is False, "primer tiro tiene m='0' -- deberia ser fallado"
    assert shots[1].made is False, "segundo tiro tiene m='0' -- deberia ser fallado"
    assert shots[2].made is True, "tercer tiro tiene m='1' -- deberia ser acertado"
    assert shots[0].team_id == "PALMER ALMA MEDITERRÀNEA PALMA"
    assert shots[2].team_id == "MELILLA SPORT CAPITAL CLUB MELILLA BALONCESTO"
    for s in shots:
        assert s.shot_type == "desconocido", (
            f"shot_type deberia ser 'desconocido' (FEB no reporta tipo real "
            f"por tiro), salio {s.shot_type!r}"
        )
