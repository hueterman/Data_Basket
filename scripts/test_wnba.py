r"""Prueba manual de humo para WnbaSource.

IMPORTANTE: ejecutar esto en tu propio equipo, en una terminal normal,
NO a traves del bridge de este chat (sin salida de red).

A diferencia de CBB, la WNBA solo tiene ~12-13 equipos, asi que
list_games() completo es practico en un smoke test (unos 12-13
peticiones a ~3.6s cada una, medio minuto en total -- ver docstring de
src/scraper/sports_reference.py para el porque de ese ritmo).

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_wnba.py [--season 2024]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper.wnba import WnbaSource, WnbaSourceError


def main() -> None:
    season = "2024"  # temporada ya finalizada a fecha de hoy -> datos completos
    args = sys.argv[1:]
    if "--season" in args:
        i = args.index("--season")
        season = args[i + 1]

    source = WnbaSource(season=season)

    teams = source.list_teams(season=season)
    print(f"list_teams: {len(teams)} equipos (season={season})")
    if teams:
        print(" ", teams[0])

    if teams:
        players = source.list_players(team_id=teams[0].team_id)
        print(f"list_players ({teams[0].name}): {len(players)} jugadoras")
        if players:
            print(" ", players[0])

    print("\nlist_games: recorriendo el calendario de cada equipo (~medio minuto)...")
    games = source.list_games(season=season)
    print(f"list_games: {len(games)} partidos")
    if not games:
        print("  (sin partidos, no se puede seguir probando boxscore/tiros)")
        return
    sample_game = games[0]
    print(" ", sample_game)

    box = source.get_boxscore(game_id=sample_game.game_id)
    print(
        f"\nget_boxscore: {len(box.team_stats)} lineas de equipo, "
        f"{len(box.player_stats)} de jugadora"
    )
    for ts in box.team_stats:
        print(" ", ts)
    if box.player_stats:
        linea = box.player_stats[0]
        print(" ", linea)
        print(f"  -> revisar aqui el formato de 'minutes': {linea.minutes!r}")

    try:
        shots = source.get_shots(game_id=sample_game.game_id)
        print(f"\nget_shots: {len(shots)} tiros")
        if shots:
            print(" ", shots[0])
    except WnbaSourceError as exc:
        print(f"\nget_shots: fallo -> {exc}")
        print("  (puede que este partido concreto no tenga grafico de tiro; "
              "prueba con otro partido de 'games' si quieres confirmar mas casos)")


if __name__ == "__main__":
    main()
