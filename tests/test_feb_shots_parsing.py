"""Prueba OFFLINE del parseo corregido de FebSource.get_shots() (ver
"BUGS REALES corregidos 2026-09-20(e)" Y "BUGS REALES corregidos
2026-09-22" en src/scraper/feb.py).

Migrado desde scripts/test_feb_shots_fix.py a pytest, y AMPLIADO
2026-09-22 con un payload real mas completo: la primera version (payload
recortado a solo {name}/{team, player, ...} de un fragmento capturado el
2026-09-20) no incluia team.id ni el roster (TEAM[].PLAYER[]), asi que
nunca pudo detectar que team_id/player_id estaban mal -- se corrigio
precisamente al re-verificar en vivo el bug ya conocido con un payload
completo. El payload de abajo es REAL, capturado en vivo el 2026-09-22
contra game_id=2220021 (Palmer Alma Mediterrania Palma - Melilla Sport
Capital, Primera FEB) via `fetch()` autenticado desde el navegador
integrado (no un JSON inventado) -- recortado a los 3 tiros de siempre
mas los jugadores reales necesarios para resolverlos (incluye el caso
real de dorsal duplicado entre los dos equipos: "no"="22" es F. RUESGA
en Palmer y N. RAKOCEVIC en Melilla, jugadores distintos).

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
            {
                "id": "858102",
                "name": "PALMER ALMA MEDITERRÀNEA PALMA",
                "PLAYER": [
                    {"id": "9356085", "no": "7", "name": "P. FIGUERAS LOPEZ"},
                    {"id": "9356081", "no": "22", "name": "F. RUESGA"},
                ],
            },
            {
                "id": "858000",
                "name": "MELILLA SPORT CAPITAL CLUB MELILLA BALONCESTO",
                "PLAYER": [
                    {"id": "9357496", "no": "0", "name": "R. HARRIS II"},
                    {"id": "9357490", "no": "22", "name": "N. RAKOCEVIC"},
                ],
            },
        ],
        "SHOTS": [
            # team 0, fallado (m="0"), dorsal 7 -> P. FIGUERAS LOPEZ (9356085)
            {"m": "0", "t": "05:26", "x": "32.64540335741655", "y": "72.6517911484462",
             "team": "0", "player": "7", "quarter": "1"},
            # team 0, otro fallado, mismo jugador
            {"m": "0", "t": "03:22", "x": "87.30456594417014", "y": "53.315238016628804",
             "team": "0", "player": "7", "quarter": "4"},
            # team 1, ACERTADO (m="1"), dorsal 0 -> R. HARRIS II (9357496)
            # -- el caso que el bug viejo tambien marcaba como True, pero
            # por la razon equivocada.
            {"m": "1", "t": "01:15", "x": "69.48092756759503", "y": "28.837173633894253",
             "team": "1", "player": "0", "quarter": "1"},
            # team 0, dorsal 22 -> F. RUESGA (9356081): mismo dorsal que el
            # jugador de team 1 de abajo, DEBE resolver al jugador de SU
            # equipo, no mezclarse entre equipos.
            {"m": "0", "t": "02:10", "x": "40.0", "y": "60.0",
             "team": "0", "player": "22", "quarter": "2"},
            # team 1, dorsal 22 -> N. RAKOCEVIC (9357490): mismo dorsal que
            # arriba, equipo distinto, jugador real distinto.
            {"m": "1", "t": "06:45", "x": "55.0", "y": "20.0",
             "team": "1", "player": "22", "quarter": "3"},
            # team 0, dorsal SIN match en el roster (caso defensivo, no
            # visto en datos reales) -- debe caer al respaldo sintetico
            # "{team_id}-{dorsal}", nunca reventar ni dejar el dorsal
            # pelado como si fuera un id real.
            {"m": "0", "t": "00:30", "x": "10.0", "y": "10.0",
             "team": "0", "player": "99", "quarter": "4"},
        ],
    }
}


def test_get_shots_resolves_real_team_and_player_ids_from_real_payload():
    src = FebSource(competition="feb", year="2026")
    src._get_live_token = MagicMock(return_value="fake-token")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json = MagicMock(return_value=REAL_SHOTCHART_PAYLOAD)
    src._session = MagicMock()
    src._session.get = MagicMock(return_value=fake_response)

    shots = src.get_shots("2220021")

    assert len(shots) == 6
    assert shots[0].made is False, "primer tiro tiene m='0' -- deberia ser fallado"
    assert shots[1].made is False, "segundo tiro tiene m='0' -- deberia ser fallado"
    assert shots[2].made is True, "tercer tiro tiene m='1' -- deberia ser acertado"

    # team_id: el ID NUMERICO REAL de TEAM[i].id (AÑADIDO 2026-09-22),
    # NUNCA el nombre del equipo (comportamiento del bug viejo).
    assert shots[0].team_id == "858102"
    assert shots[2].team_id == "858000"
    for s in shots:
        assert s.team_id in {"858102", "858000"}, (
            f"team_id deberia ser el ID numerico real de TEAM[i].id, "
            f"nunca el nombre del equipo -- salio {s.team_id!r}"
        )

    # player_id: el ID real resuelto via el roster de SU equipo (AÑADIDO
    # 2026-09-22), nunca el dorsal ("player") pelado.
    assert shots[0].player_id == "9356085", "dorsal 7 de Palmer -> P. FIGUERAS LOPEZ"
    assert shots[1].player_id == "9356085"
    assert shots[2].player_id == "9357496", "dorsal 0 de Melilla -> R. HARRIS II"

    # Dorsal duplicado ENTRE equipos (no='22') -- debe resolver al
    # jugador real de CADA equipo, nunca mezclarse.
    assert shots[3].team_id == "858102" and shots[3].player_id == "9356081", (
        "dorsal 22 de Palmer deberia ser F. RUESGA (9356081)"
    )
    assert shots[4].team_id == "858000" and shots[4].player_id == "9357490", (
        "dorsal 22 de Melilla deberia ser N. RAKOCEVIC (9357490), un "
        "jugador DISTINTO al dorsal 22 de Palmer pese a compartir dorsal"
    )

    # Dorsal sin match en el roster -- respaldo sintetico "{team_id}-
    # {dorsal}" (mismo patron que list_players()/get_boxscore()), nunca
    # el dorsal pelado como si fuera un id real ni un player_id vacio.
    assert shots[5].player_id == "858102-99"

    for s in shots:
        assert s.shot_type == "desconocido", (
            f"shot_type deberia ser 'desconocido' (FEB no reporta tipo real "
            f"por tiro), salio {s.shot_type!r}"
        )


def test_get_shots_falls_back_to_slug_when_team_id_missing():
    """Caso defensivo (no visto en datos reales, ver docstring de
    get_shots()): si el payload no trajera team.id por algun motivo,
    debe caer al slug del nombre -- mismo criterio de respaldo que el
    resto del modulo -- nunca al nombre completo sin normalizar."""
    payload = {
        "SHOTCHART": {
            "TEAM": [
                {"name": "Equipo Sin Id"},
                {"id": "858000", "name": "MELILLA SPORT CAPITAL CLUB MELILLA BALONCESTO"},
            ],
            "SHOTS": [
                {"m": "1", "t": "01:00", "x": "1.0", "y": "1.0",
                 "team": "0", "player": "5", "quarter": "1"},
            ],
        }
    }
    src = FebSource(competition="feb", year="2026")
    src._get_live_token = MagicMock(return_value="fake-token")
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json = MagicMock(return_value=payload)
    src._session = MagicMock()
    src._session.get = MagicMock(return_value=fake_response)

    shots = src.get_shots("2220021")
    assert len(shots) == 1
    assert shots[0].team_id != "Equipo Sin Id", "nunca el nombre completo sin normalizar"
    assert shots[0].team_id, "debe caer a un slug no vacio, no reventar"
