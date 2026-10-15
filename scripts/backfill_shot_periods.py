"""Rellena el PERIODO (cuarto/prorroga) de los tiros ya guardados.

Los tiros ingestados antes de guardar el periodo tienen `period` NULL, y la cache
`data/raw/.../shots/*.json` tampoco lo trae, asi que hay que volver a pedir los tiros
de cada partido a la fuente (solo los tiros: boxscores y jugadores no se tocan).

Solo procesa los partidos con algun tiro sin periodo -> se puede cortar y reanudar:
al repetir el comando continua donde se quedo. Si la fuente limita las peticiones
(RateLimitedError) para en seco y avisa; vuelve a lanzarlo mas tarde.

Fuentes con periodo disponible: nba, euroleague, eurocup, nextgen, wnba.

Uso (desde la raiz del proyecto, con el venv activo):
    python scripts/backfill_shot_periods.py --sources euroleague,eurocup          # cuenta y pide confirmar con --apply
    python scripts/backfill_shot_periods.py --sources euroleague --apply
    python scripts/backfill_shot_periods.py --sources nba --seasons 2024-25 --apply --limit 300
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline import raw_cache  # noqa: E402
from src.pipeline.db import DEFAULT_DB_PATH, get_connection  # noqa: E402
from src.pipeline.repository import replace_shots  # noqa: E402
from src.scraper.sports_reference import RateLimitedError  # noqa: E402

SUPPORTED = ("nba", "euroleague", "eurocup", "nextgen", "wnba")


def build_source(name: str, season: str):
    if name == "nba":
        from src.scraper.nba import NbaSource
        return NbaSource(season=season)
    if name in ("euroleague", "eurocup", "nextgen"):
        from src.scraper.euroleague import EuroleagueSource
        return EuroleagueSource(competition=name, season=season)
    if name == "wnba":
        from src.scraper.wnba import WnbaSource
        return WnbaSource(season=season)
    raise ValueError(f"fuente sin periodo de tiro: {name!r}")


def pending(conn, source: str, seasons: list[str] | None) -> dict[str, list[str]]:
    """{temporada: [game_id, ...]} con tiros guardados y alguno sin periodo."""
    rows = conn.execute(
        """
        SELECT g.season, g.game_id FROM games g
        WHERE g.source = ?
          AND EXISTS (SELECT 1 FROM shots s WHERE s.source = g.source AND s.game_id = g.game_id
                      AND s.period IS NULL)
        ORDER BY g.season, g.date, g.game_id
        """,
        (source,),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for season, game_id in rows:
        season = (season or "").strip()
        if seasons and season not in seasons:
            continue
        out.setdefault(season, []).append(game_id)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default="euroleague,eurocup", help="lista separada por comas")
    ap.add_argument("--seasons", default="", help="lista separada por comas (por defecto, todas las pendientes)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ap.add_argument("--limit", type=int, default=0, help="maximo de partidos a procesar en esta ejecucion")
    ap.add_argument("--sleep", type=float, default=0.0, help="segundos de pausa extra entre partidos")
    ap.add_argument("--apply", action="store_true", help="sin esto solo cuenta lo pendiente")
    args = ap.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()] or None
    bad = [s for s in sources if s not in SUPPORTED]
    if bad:
        print(f"ERROR: fuente(s) sin periodo de tiro: {', '.join(bad)}. Validas: {', '.join(SUPPORTED)}", file=sys.stderr)
        return 1

    conn = get_connection(args.db)
    done = failed = 0
    try:
        for source in sources:
            todo = pending(conn, source, seasons)
            n = sum(len(v) for v in todo.values())
            print(f"[{source}] {n} partidos con tiros sin periodo en {len(todo)} temporada(s)")
            if not args.apply:
                for season, ids in todo.items():
                    print(f"    {season}: {len(ids)}")
                continue
            for season, ids in todo.items():
                src = build_source(source, season)
                print(f"[{source}] {season}: {len(ids)} partidos")
                for i, game_id in enumerate(ids, 1):
                    if args.limit and done >= args.limit:
                        print(f"Limite de {args.limit} partidos alcanzado; vuelve a lanzarlo para seguir.")
                        return 0
                    try:
                        shots = src.get_shots(game_id=game_id)
                    except RateLimitedError as exc:
                        print(f"PARADO: la fuente limita las peticiones ({exc}). Reintenta mas tarde.")
                        return 2
                    except Exception as exc:  # partido concreto: se avisa y se sigue
                        failed += 1
                        print(f"  aviso: {game_id} -> {exc}")
                        continue
                    if not shots or all(s.period is None for s in shots):
                        failed += 1
                        print(f"  aviso: {game_id} sin periodo en la fuente; se deja como estaba")
                        continue
                    raw_cache.save_shots(source, season, game_id, shots)
                    replace_shots(conn, source, game_id, shots)
                    done += 1
                    if i % 25 == 0 or i == len(ids):
                        print(f"  {i}/{len(ids)}")
                    if args.sleep:
                        time.sleep(args.sleep)
    finally:
        conn.close()
    if args.apply:
        print(f"Listo: {done} partidos actualizados, {failed} con aviso.")
    else:
        print("Esto ha sido solo el recuento. Anade --apply para descargar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
