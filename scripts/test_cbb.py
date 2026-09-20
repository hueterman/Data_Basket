r"""Prueba manual de humo para CbbSource.

IMPORTANTE: ejecutar esto en tu propio equipo, en una terminal normal,
NO a traves del bridge de este chat (sin salida de red).

CBB es la fuente MAS ESTRICTA del proyecto en cuanto a ritmo de
peticiones (ver docstring de src/scraper/sports_reference.py): cada
peticion espera ~3.6s antes de salir. Por eso este script NO llama a
list_games() completo por defecto -- recorrer una temporada entera
(~180 dias) tardaria mas de 10 minutos reales y son ~180 peticiones,
solo para un smoke test. En su lugar:

  - Prueba list_teams / list_players / get_shots directamente.
  - Para probar la logica de partidos y boxscore SIN adivinar un
    game_id a mano, pide un unico dia real del marcador diario (el
    metodo interno que list_games() llama dia a dia) y usa el primer
    partido real que devuelva para probar get_boxscore.
  - Si quieres probar list_games() completo (la temporada entera, con
    el coste real de tiempo que eso conlleva), ejecuta:
        python scripts\test_cbb.py --full-season

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_cbb.py [--full-season] [--gender men|women] [--season 2024]
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper.cbb import CbbSource, CbbSourceError

# Dia real de temporada regular con partidos ya jugados (principio de
# temporada 2023-24, "season"="2024" en la convencion de este modulo) --
# se usa solo para acotar la prueba a un unico dia en vez de recorrer
# la temporada entera; el game_id real se toma de la respuesta, nunca
# se adivina a mano (ver docstring del modulo).
EJEMPLO_DIA = date(2023, 11, 10)


def main() -> None:
    gender = "men"
    season = "2024"
    full_season = False
    args = sys.argv[1:]
    if "--full-season" in args:
        full_season = True
        args.remove("--full-season")
    if "--gender" in args:
        i = args.index("--gender")
        gender = args[i + 1]
        del args[i:i + 2]
    if "--season" in args:
        i = args.index("--season")
        season = args[i + 1]
        del args[i:i + 2]

    source = CbbSource(gender=gender, season=season)

    teams = source.list_teams(season=season)
    print(f"list_teams: {len(teams)} equipos ({gender}, season={season})")
    if teams:
        print(" ", teams[0])

    if teams:
        players = source.list_players(team_id=teams[0].team_id)
        print(f"list_players ({teams[0].name}): {len(players)} jugadores")
        if players:
            print(" ", players[0])

    if full_season:
        print(
            "\n--full-season: recorriendo la temporada entera dia a dia -- "
            "esto tarda varios minutos reales (ver aviso en sports_reference.py)."
        )
        games = source.list_games(season=season)
        print(f"list_games: {len(games)} partidos")
        sample_game_id = games[0].game_id if games else None
    else:
        print(f"\nProbando un unico dia real del marcador diario ({EJEMPLO_DIA.isoformat()})...")
        day_games = source._list_games_for_day(EJEMPLO_DIA, season=season)
        print(f"  {len(day_games)} partidos ese dia")
        sample_game_id = None
        for g in day_games:
            print(" ", g)
            if sample_game_id is None:
                sample_game_id = g.game_id

    if sample_game_id is None:
        print("\nNo se encontro ningun game_id real para probar get_boxscore -- "
              "prueba con otro --season/--gender/dia.")
        return

    box = source.get_boxscore(game_id=sample_game_id)
    print(
        f"\nget_boxscore({sample_game_id}): {len(box.team_stats)} lineas de "
        f"equipo, {len(box.player_stats)} de jugador"
    )
    for ts in box.team_stats:
        print(" ", ts)
    if box.player_stats:
        print(" ", box.player_stats[0])

    try:
        shots = source.get_shots(game_id=sample_game_id)
        print(f"get_shots: {len(shots)} tiros")
    except CbbSourceError as exc:
        print(f"get_shots: NO IMPLEMENTADO (esperado, confirmado ausente) -> {exc}")


if __name__ == "__main__":
    main()
