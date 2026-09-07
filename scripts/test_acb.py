r"""Prueba manual de humo para AcbSource.

A diferencia de nba.py y euroleague.py, este modulo tiene dos metodos
sin implementar todavia (list_games, get_shots) -- ver docstring de
acb.py para el porque. Este script prueba solo lo que SI esta
implementado: list_teams, list_players, get_boxscore (con un partido
real ya jugado, hay que pasar su game_id a mano porque list_games()
no existe todavia para encontrarlo).

IMPORTANTE: ejecutar esto en tu propio equipo, en una terminal normal,
NO a traves del bridge de este chat (sin salida de red en absoluto).

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_acb.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper.acb import AcbSource, AcbSourceError

# Partido real ya jugado, encontrado a mano el 7-sep-2026 (ver conversacion):
# Real Madrid 81-71 Dreamland Gran Canaria, 5-oct-2025.
EJEMPLO_GAME_ID = "real-madrid-vs-dreamland-gran-canaria-104464"


def main() -> None:
    source = AcbSource()

    teams = source.list_teams(season=None)
    print(f"list_teams: {len(teams)} equipos")
    if teams:
        print(" ", teams[0])

    if teams:
        players = source.list_players(team_id=teams[0].team_id)
        print(f"list_players ({teams[0].name}): {len(players)} jugadores")
        if players:
            print(" ", players[0])

    try:
        games = source.list_games(season="90")
        print(f"list_games: {len(games)} partidos")
    except AcbSourceError as exc:
        print(f"list_games: NO IMPLEMENTADO (esperado) -> {exc}")

    box = source.get_boxscore(game_id=EJEMPLO_GAME_ID)
    print(
        f"get_boxscore: {len(box.team_stats)} lineas de equipo, "
        f"{len(box.player_stats)} de jugador"
    )
    for ts in box.team_stats:
        print(" ", ts)
    if box.player_stats:
        print(" ", box.player_stats[0])

    try:
        shots = source.get_shots(game_id=EJEMPLO_GAME_ID)
        print(f"get_shots: {len(shots)} tiros")
    except AcbSourceError as exc:
        print(f"get_shots: NO IMPLEMENTADO (esperado) -> {exc}")


if __name__ == "__main__":
    main()
