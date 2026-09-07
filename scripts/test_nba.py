r"""Prueba manual de humo para NbaSource.

nba.py no tiene punto de entrada propio (es un modulo de libreria, pensado
para ser importado desde el pipeline, no para ejecutarse solo) -- este
script sirve para probarlo a mano contra la API real.

IMPORTANTE: ejecutar esto en tu propio equipo, en una terminal normal
(PowerShell/cmd), NO a traves del bridge de este chat -- el VM que usa el
bridge tiene bloqueada la salida de red hacia stats.nba.com.

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_nba.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Asegura que la raiz del proyecto esta en sys.path, para que
# "from src.scraper.base import ..." (import absoluto) funcione sin
# importar cómo se invoque este script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper.nba import NbaSource


def main() -> None:
    season = "2025-26"  # temporada ya finalizada a fecha de hoy -> datos completos
    source = NbaSource(season=season)

    teams = source.list_teams(season=season)
    print(f"list_teams: {len(teams)} equipos")
    print(" ", teams[0])

    lakers = next(t for t in teams if t.name == "Los Angeles Lakers")
    players = source.list_players(team_id=lakers.team_id)
    print(f"list_players ({lakers.name}): {len(players)} jugadores")
    if players:
        print(" ", players[0])

    games = source.list_games(season=season)
    print(f"list_games: {len(games)} partidos")
    if not games:
        print("  (sin partidos, no se puede seguir probando boxscore/tiros)")
        return
    sample_game = games[0]
    print(" ", sample_game)

    box = source.get_boxscore(game_id=sample_game.game_id)
    print(
        f"get_boxscore: {len(box.team_stats)} lineas de equipo, "
        f"{len(box.player_stats)} de jugador"
    )
    if box.player_stats:
        linea = box.player_stats[0]
        print(" ", linea)
        print(f"  -> revisar aqui el formato de 'minutes': {linea.minutes!r}")

    shots = source.get_shots(game_id=sample_game.game_id)
    print(f"get_shots: {len(shots)} tiros")
    if shots:
        print(" ", shots[0])


if __name__ == "__main__":
    main()
