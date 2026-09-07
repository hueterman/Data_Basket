r"""Prueba manual de humo para FebSource.

Como con acb.py, hay metodos sin implementar todavia (list_players,
get_shots) -- ver docstring de feb.py. La incertidumbre aqui es mayor
que en los demas modulos: ni el mapeo de columnas del boxscore ni el
formato exacto de texto de resultados.aspx se pudieron confirmar
contra un partido ya jugado (la temporada 2026-27 no ha empezado
todavia). Este script sirve sobre todo para ver DONDE falla, si falla.

IMPORTANTE: ejecutar esto en tu propio equipo, en una terminal normal,
NO a traves del bridge de este chat (sin salida de red en absoluto).

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_feb.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper.feb import FebSource, FebSourceError


def main() -> None:
    # Primera FEB, temporada que el servidor muestre por defecto
    # (t=2026 se ignora en la practica, ver docstring de feb.py).
    source = FebSource(group_code="1", year="2026", slug="primerafeb")

    games = source.list_games(season=None)
    print(f"list_games: {len(games)} partidos (jornada por defecto del servidor)")
    if games:
        print(" ", games[0])

    teams = source.list_teams(season=None)
    print(f"list_teams: {len(teams)} equipos (derivados de list_games)")
    if teams:
        print(" ", teams[0])

    try:
        players = source.list_players(team_id=teams[0].team_id if teams else "equipo")
        print(f"list_players: {len(players)} jugadores")
    except FebSourceError as exc:
        print(f"list_players: NO IMPLEMENTADO (esperado) -> {exc}")

    if games:
        game_id = games[0].game_id
        try:
            box = source.get_boxscore(game_id=game_id)
            print(
                f"get_boxscore({game_id}): {len(box.team_stats)} lineas de "
                f"equipo, {len(box.player_stats)} de jugador"
            )
            if box.player_stats:
                print(" ", box.player_stats[0])
        except FebSourceError as exc:
            print(f"get_boxscore({game_id}): fallo (puede ser normal si el "
                  f"partido aun no se ha jugado) -> {exc}")

        try:
            shots = source.get_shots(game_id=game_id)
            print(f"get_shots: {len(shots)} tiros")
        except FebSourceError as exc:
            print(f"get_shots: NO IMPLEMENTADO (esperado) -> {exc}")


if __name__ == "__main__":
    main()
