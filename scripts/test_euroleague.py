r"""Prueba manual de humo para EuroleagueSource.

A diferencia de nba.py, este modulo se escribio ya verificando la forma
real de las respuestas (con fetch, ver docstring de euroleague.py) y se
valido offline contra fixtures de esas mismas respuestas -- pero eso no
sustituye a probarlo contra la API en vivo, con una temporada completa.

IMPORTANTE: ejecutar esto en tu propio equipo, en una terminal normal,
NO a traves del bridge de este chat (sin salida de red en absoluto).

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_euroleague.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper.euroleague import EuroleagueSource


def probar(competition: str, season: str) -> None:
    print(f"\n=== {competition} (season={season}) ===")
    source = EuroleagueSource(competition=competition, season=season)

    teams = source.list_teams(season=season)
    print(f"list_teams: {len(teams)} equipos")
    if not teams:
        print("  (sin equipos, no se puede seguir probando)")
        return
    print(" ", teams[0])

    players = source.list_players(team_id=teams[0].team_id)
    print(f"list_players ({teams[0].name}): {len(players)} jugadores")
    if players:
        print(" ", players[0])

    games = source.list_games(season=season)
    print(f"list_games: {len(games)} partidos")
    played_games = [g for g in games if g.played]
    if not played_games:
        print("  (sin partidos jugados todavia, no se puede seguir probando)")
        return
    sample_game = played_games[0]
    print(" ", sample_game)

    box = source.get_boxscore(game_id=sample_game.game_id)
    print(
        f"get_boxscore: {len(box.team_stats)} lineas de equipo, "
        f"{len(box.player_stats)} de jugador"
    )
    if box.player_stats:
        print(" ", box.player_stats[0])

    shots = source.get_shots(game_id=sample_game.game_id)
    print(f"get_shots: {len(shots)} tiros")
    if shots:
        print(" ", shots[0])


if __name__ == "__main__":
    probar("euroleague", "E2024")
    probar("eurocup", "U2024")
