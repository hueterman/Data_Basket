r"""Prueba manual de humo para la capa de almacenamiento normalizado
(src/pipeline/): ingiere una fuente real de punta a punta (equipos,
jugadores/as, partidos, boxscores, tiros) en una base de datos SQLite de
prueba, y hace unas consultas reales sobre lo guardado para poder
comprobar a ojo que los datos aterrizaron bien.

IMPORTANTE: ejecutar esto en tu propio equipo, en una terminal normal,
NO a traves del bridge de este chat (sin salida de red).

Por defecto usa WNBA, season=2024 (temporada ya cerrada, ~12-13 equipos,
la fuente mas barata de las que ya tienen tiros) y solo procesa 5
partidos (--limit) para que la prueba sea rapida -- no es una ingesta
real de produccion, ver el aviso de coste en el docstring de
src/pipeline/ingest.py antes de lanzar algo mas grande (sobre todo con
--source cbb, list_games() ya tarda largo por si solo, y una temporada
CBB completa de boxscores puede tardar horas).

ACB y FEB: a fecha de esta prueba la temporada 2026-27 aun no ha
empezado (0 partidos jugados confirmado, ver decisiones_proyecto.md) --
con estas dos fuentes es normal y esperado ver "0 boxscores" al final,
no es un fallo del pipeline.

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_storage.py [--source wnba] [--season 2024] [--limit 5] [--no-players]
    python scripts\test_storage.py --source cbb --gender men --season 2024 --limit 5
    python scripts\test_storage.py --source feb --feb-group 1 --feb-year 2026 --feb-slug primerafeb
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import get_connection, ingest_source
from src.scraper.acb import AcbSource
from src.scraper.cbb import CbbSource
from src.scraper.euroleague import EuroleagueSource
from src.scraper.feb import FebSource
from src.scraper.nba import NbaSource
from src.scraper.wnba import WnbaSource

DEFAULT_SEASONS = {
    "nba": "2025-26",
    "wnba": "2024",
    "cbb": "2024",
    "euroleague": "E2024",
    "eurocup": "U2024",
    "nextgen": "2024",
}


def build_source(name: str, args: dict) -> object:
    season = args.get("season")
    if name == "acb":
        return AcbSource()
    if name == "feb":
        return FebSource(
            group_code=args.get("feb_group", "1"),
            year=args.get("feb_year", "2026"),
            slug=args.get("feb_slug", "primerafeb"),
        )
    if name == "nba":
        return NbaSource(season=season or DEFAULT_SEASONS["nba"])
    if name == "wnba":
        return WnbaSource(season=season or DEFAULT_SEASONS["wnba"])
    if name == "cbb":
        return CbbSource(gender=args.get("gender", "men"), season=season or DEFAULT_SEASONS["cbb"])
    if name in ("euroleague", "eurocup", "nextgen"):
        return EuroleagueSource(competition=name, season=season)
    raise ValueError(f"--source desconocido: {name!r}")


def season_for(name: str, args: dict) -> str:
    """La temporada que se pasa a ingest_source() -- para acb/feb, que no
    tienen 'season' explicita en su interfaz, se usa el año como
    etiqueta informativa (no afecta a que datos se piden, ver docstrings
    de acb.py/feb.py sobre por que ignoran/derivan la temporada solos)."""
    if args.get("season"):
        return args["season"]
    if name == "acb":
        return "2026-27"
    if name == "feb":
        return f"{args.get('feb_year', '2026')}-{args.get('feb_group', '1')}-{args.get('feb_slug', 'primerafeb')}"
    return DEFAULT_SEASONS.get(name, "desconocida")


def parse_args() -> dict:
    argv = sys.argv[1:]
    args = {"source": "wnba", "limit": 5, "fetch_players": True}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--source":
            args["source"] = argv[i + 1]; i += 2
        elif arg == "--season":
            args["season"] = argv[i + 1]; i += 2
        elif arg == "--limit":
            args["limit"] = int(argv[i + 1]); i += 2
        elif arg == "--gender":
            args["gender"] = argv[i + 1]; i += 2
        elif arg == "--feb-group":
            args["feb_group"] = argv[i + 1]; i += 2
        elif arg == "--feb-year":
            args["feb_year"] = argv[i + 1]; i += 2
        elif arg == "--feb-slug":
            args["feb_slug"] = argv[i + 1]; i += 2
        elif arg == "--no-players":
            args["fetch_players"] = False; i += 1
        else:
            raise SystemExit(f"Argumento no reconocido: {arg!r}")
    return args


def main() -> None:
    args = parse_args()
    source_name = args["source"]
    source_obj = build_source(source_name, args)
    season = season_for(source_name, args)

    tmp_dir = Path(tempfile.mkdtemp(prefix="test_storage_"))
    db_path = tmp_dir / "basketball.db"
    raw_dir = tmp_dir / "raw"
    print(f"Base de datos de prueba: {db_path}")
    print(f"Cache raw de prueba:     {raw_dir}\n")

    summary = ingest_source(
        source_obj,
        season=season,
        db_path=db_path,
        raw_dir=raw_dir,
        fetch_players=args["fetch_players"],
        limit=args["limit"],
    )
    print(f"\n{summary}\n")

    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row

    print("--- Comprobación real sobre la base de datos ---")
    for table in ("teams", "players", "games", "team_stats", "player_stats", "shots"):
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE source = ?", (source_obj.source,)).fetchone()["n"]
        print(f"  {table:<14} {n} filas")

    row = conn.execute(
        """
        SELECT game_id, date, home_team_id, away_team_id, home_score, away_score
        FROM games
        WHERE source = ? AND played = 1
        ORDER BY date
        LIMIT 1
        """,
        (source_obj.source,),
    ).fetchone()

    if row is None:
        print(
            "\n(No hay partidos jugados en esta selección todavía -- normal si "
            "es ACB/FEB con la temporada 2026-27 sin empezar. Nada más que "
            "comprobar.)"
        )
        conn.close()
        return

    game_id = row["game_id"]
    print(f"\nPartido de muestra: {game_id} ({row['date']}) -- marcador real de games: "
          f"{row['home_team_id']} {row['home_score']} - {row['away_score']} {row['away_team_id']}")

    ts_rows = conn.execute(
        "SELECT team_id, val, t2_made, t3_made, ft_made FROM team_stats "
        "WHERE source = ? AND game_id = ?",
        (source_obj.source, game_id),
    ).fetchall()
    for ts in ts_rows:
        print(f"  team_stats: {ts['team_id']} val={ts['val']} "
              f"(t2={ts['t2_made']} t3={ts['t3_made']} ft={ts['ft_made']})")

    top_scorer = conn.execute(
        "SELECT player_name, team_id, points, val FROM player_stats "
        "WHERE source = ? AND game_id = ? ORDER BY points DESC LIMIT 1",
        (source_obj.source, game_id),
    ).fetchone()
    if top_scorer:
        print(f"  máximo anotador: {top_scorer['player_name']} ({top_scorer['team_id']}) "
              f"-- {top_scorer['points']} puntos, val={top_scorer['val']}")

    n_shots = conn.execute(
        "SELECT COUNT(*) AS n FROM shots WHERE source = ? AND game_id = ?",
        (source_obj.source, game_id),
    ).fetchone()["n"]
    print(f"  tiros guardados para este partido: {n_shots}")

    conn.close()


if __name__ == "__main__":
    main()
