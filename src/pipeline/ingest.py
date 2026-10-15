"""Orquestador: de un BasketballSource en vivo (o de la cache) a la base
de datos normalizada.

`ingest_source()` es el unico punto de entrada que deberia usar el resto
del proyecto (notebooks, scripts/train.py mas adelante): recibe CUALQUIER
objeto que implemente `BasketballSource` (AcbSource, CbbSource...) y una
temporada, y hace equipos -> jugadores -> partidos -> boxscores -> tiros,
cacheando cada respuesta en `data/raw/` (ver `raw_cache.py`) antes de
escribirla en SQLite (ver `repository.py`).

AVISO DE COSTE REAL, sobre todo para CBB: recorrer boxscore a boxscore
una temporada completa de CBB (~360-400 escuelas D1, miles de partidos)
a 3.6s/peticion puede tardar VARIAS HORAS -- no es un numero exagerado,
es aritmetica directa sobre el rate limit ya documentado en
sports_reference.py. `game_filter`/`limit` existen para poder probar (o
ingerir a proposito solo un trozo, p. ej. un mes) sin comprometerse a esa
duracion completa cada vez. `scripts/test_storage.py` usa estos parametros
por eso mismo, igual que `scripts/test_cbb.py` ya evitaba list_games()
completo por defecto.

Regla de parada NO negociable: `RateLimitedError` (definida en
sports_reference.py, se lanza ante un HTTP 429 real) SIEMPRE se
repropaga y aborta la ingesta entera, nunca se trata como "este partido
en concreto fallo, sigo con el siguiente" -- ver el docstring de
sports_reference.py sobre por que un 429 puede significar bloqueo de
IP/sesion hasta 24h. Cualquier otro error durante get_boxscore()/
get_shots() de UN partido concreto SI se trata como recuperable: se
avisa y se sigue con el resto (mismo criterio "avisa y omite, no
revienta" ya aplicado dentro de cbb.py/acb.py/feb.py).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from src.pipeline import raw_cache
from src.pipeline.db import DEFAULT_DB_PATH, get_connection
from src.pipeline.raw_cache import DEFAULT_RAW_DIR
from src.pipeline.repository import (
    log_stage,
    replace_shots,
    upsert_boxscore,
    upsert_games,
    upsert_players,
    upsert_sources,
    upsert_teams,
)
from src.pipeline import clubs
from src.scraper.base import BasketballSource, BoxscoreNotPublished, GameRef

try:
    from src.scraper.sports_reference import RateLimitedError
except ImportError:  # pragma: no cover - solo aplica a fuentes no-Sports Reference
    class RateLimitedError(RuntimeError):  # type: ignore[no-redef]
        """Placeholder si sports_reference.py no esta disponible (fuentes
        que no son CBB/WNBA no lo importan nunca en la practica)."""


class ProgressFn(Protocol):
    def __call__(self, message: str) -> None: ...


def _default_progress(message: str) -> None:
    print(message, flush=True)


@dataclass
class IngestSummary:
    source: str
    season: str
    n_teams: int = 0
    n_players: int = 0
    n_games: int = 0
    n_boxscores_ok: int = 0
    n_boxscores_failed: int = 0
    n_boxscores_no_stats: int = 0   # la fuente no publica estadisticas (no es un fallo)
    n_shots_ok: int = 0
    n_shots_unavailable: int = 0
    failed_game_ids: list[str] = field(default_factory=list)
    no_stats_game_ids: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def __str__(self) -> str:  # resumen legible para consola/notebook
        return (
            f"IngestSummary({self.source}, {self.season}): "
            f"{self.n_teams} equipos, {self.n_players} jugadores/as, "
            f"{self.n_games} partidos, "
            f"{self.n_boxscores_ok} boxscores ok "
            f"({self.n_boxscores_failed} fallidos"
            + (f", {self.n_boxscores_no_stats} sin estadisticas en la fuente"
               if self.n_boxscores_no_stats else "")
            + f"), {self.n_shots_ok} partidos con tiros guardados "
            f"({self.n_shots_unavailable} sin tiros disponibles), "
            f"{self.elapsed_seconds:.1f}s"
        )


def ingest_source(
    source_obj: BasketballSource,
    season: str,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    fetch_players: bool = True,
    fetch_boxscores: bool = True,
    fetch_shots: bool = True,
    only_played: bool = True,
    game_filter: Callable[[GameRef], bool] | None = None,
    limit: int | None = None,
    use_cache: bool = True,
    progress: ProgressFn = _default_progress,
) -> IngestSummary:
    """Ingesta completa de una fuente/temporada. Ver el docstring del
    modulo sobre el coste real y la regla de RateLimitedError.

    `conn`: si no se pasa, se abre (y se cierra al terminar) una conexion
    a `db_path` con `get_connection()`.
    `use_cache`: si hay un fichero ya cacheado en `data/raw/` para una
    llamada, se usa ese en vez de volver a pedirlo -- clave para poder
    reanudar una ingesta de CBB cortada a mitad. Pasar `False` para
    forzar rescrapear todo (p. ej. si se sabe que la fuente ha cambiado).
    `game_filter`/`limit`: acotan que partidos se procesan en las etapas
    de boxscore/tiros (las mas caras) -- por defecto se procesan todos
    los partidos jugados.
    """
    started = time.monotonic()
    raw_dir = Path(raw_dir)
    owns_conn = conn is None
    if conn is None:
        conn = get_connection(db_path)
    summary = IngestSummary(source=source_obj.source, season=season)
    source = source_obj.source

    try:
        upsert_sources(conn)

        # --- equipos ---------------------------------------------------
        stage_start = datetime.now(timezone.utc).isoformat()
        teams = None
        if use_cache:
            teams = raw_cache.load_teams(source, season, raw_dir)
        if teams is None:
            progress(f"[{source}] list_teams(season={season!r})...")
            teams = source_obj.list_teams(season=season)
            raw_cache.save_teams(source, season, teams, raw_dir)
        summary.n_teams = upsert_teams(conn, teams)
        log_stage(
            conn, source=source, season=season, stage="teams",
            n_items=summary.n_teams, started_at=stage_start,
        )
        progress(f"[{source}] {summary.n_teams} equipos guardados.")

        # --- jugadores/as (por equipo) -----------------------------------
        if fetch_players:
            stage_start = datetime.now(timezone.utc).isoformat()
            n_players = 0
            for i, team in enumerate(teams, start=1):
                players = None
                if use_cache:
                    players = raw_cache.load_players(source, season, team.team_id, raw_dir)
                if players is None:
                    progress(
                        f"[{source}] list_players({team.team_id!r}) "
                        f"({i}/{len(teams)})..."
                    )
                    players = source_obj.list_players(team_id=team.team_id)
                    raw_cache.save_players(source, season, team.team_id, players, raw_dir)
                n_players += upsert_players(conn, players)
            summary.n_players = n_players
            log_stage(
                conn, source=source, season=season, stage="players",
                n_items=n_players, started_at=stage_start,
            )
            progress(f"[{source}] {n_players} jugadores/as guardados/as.")

        # --- partidos ------------------------------------------------------
        stage_start = datetime.now(timezone.utc).isoformat()
        games = None
        if use_cache:
            games = raw_cache.load_games(source, season, raw_dir)
        if games is None:
            progress(
                f"[{source}] list_games(season={season!r})... "
                "(puede tardar bastante -- ver aviso de coste en el docstring del modulo)"
            )
            games = source_obj.list_games(season=season)
            raw_cache.save_games(source, season, games, raw_dir)
        summary.n_games = upsert_games(conn, games)
        log_stage(
            conn, source=source, season=season, stage="games",
            n_items=summary.n_games, started_at=stage_start,
        )
        progress(f"[{source}] {summary.n_games} partidos guardados.")

        # --- boxscores + tiros (por partido) --------------------------------
        target_games = [g for g in games if (g.played or not only_played)]
        if game_filter is not None:
            target_games = [g for g in target_games if game_filter(g)]
        if limit is not None:
            target_games = target_games[:limit]

        if fetch_boxscores and target_games:
            stage_start = datetime.now(timezone.utc).isoformat()
            for i, game in enumerate(target_games, start=1):
                try:
                    box = None
                    if use_cache:
                        box = raw_cache.load_boxscore(source, season, game.game_id, raw_dir)
                    if box is None:
                        box = source_obj.get_boxscore(game_id=game.game_id)
                        raw_cache.save_boxscore(source, season, box, raw_dir)
                    upsert_boxscore(conn, source, box)
                    summary.n_boxscores_ok += 1
                except RateLimitedError:
                    raise  # nunca se traga -- ver docstring del modulo
                except BoxscoreNotPublished:
                    # La fuente no publica estadisticas de este partido: no es un
                    # fallo (reintentar no sirve). Se cuenta aparte, sin aviso por partido.
                    summary.n_boxscores_no_stats += 1
                    summary.no_stats_game_ids.append(game.game_id)
                except Exception as exc:  # partido concreto: se avisa y se sigue
                    summary.n_boxscores_failed += 1
                    summary.failed_game_ids.append(game.game_id)
                    progress(f"[{source}]   aviso: boxscore {game.game_id} fallo -> {exc}")

                if fetch_shots:
                    try:
                        shots = None
                        if use_cache:
                            shots = raw_cache.load_shots(source, season, game.game_id, raw_dir)
                        if shots is None:
                            shots = source_obj.get_shots(game_id=game.game_id)
                            raw_cache.save_shots(source, season, game.game_id, shots, raw_dir)
                        replace_shots(conn, source, game.game_id, shots)
                        summary.n_shots_ok += 1
                    except RateLimitedError:
                        raise  # nunca se traga -- ver docstring del modulo
                    except Exception:
                        # Fuente sin grafico de tiro para este partido (o en
                        # absoluto, p. ej. CBB/ACB) -- caso esperado, no un
                        # fallo real. No se distingue por tipo de excepcion
                        # concreto porque cada fuente lanza la suya propia
                        # (AcbSourceError, CbbSourceError...); todas heredan
                        # de RuntimeError salvo RateLimitedError, ya excluida
                        # arriba.
                        summary.n_shots_unavailable += 1

                if i % 25 == 0 or i == len(target_games):
                    progress(
                        f"[{source}]   boxscores {i}/{len(target_games)} "
                        f"({summary.n_boxscores_ok} ok, {summary.n_boxscores_failed} fallidos"
                        + (f", {summary.n_boxscores_no_stats} sin estadisticas"
                           if summary.n_boxscores_no_stats else "")
                        + ")"
                    )
            log_stage(
                conn, source=source, season=season, stage="boxscores+shots",
                n_items=summary.n_boxscores_ok, n_errors=summary.n_boxscores_failed,
                started_at=stage_start,
            )
        # Agrupa los team_id de un mismo club entre temporadas (ver clubs.py). Nunca
        # debe tumbar una ingesta ya hecha: si falla, se avisa y se sigue.
        try:
            clubs.rebuild(conn, [source])
        except Exception as exc:  # noqa: BLE001
            progress(f"[{source}] aviso: no se pudo actualizar team_club -> {exc}")
    finally:
        summary.elapsed_seconds = time.monotonic() - started
        if owns_conn:
            conn.close()

    return summary
