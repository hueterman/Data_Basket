"""Cache en disco de lo que devuelve cada scraper, antes de normalizar.

Esta es la mitad "data/raw/" de la separacion que pide el plano (seccion
09, fase 3): cada respuesta de un metodo de `BasketballSource` se guarda
tal cual (como JSON, un fichero por entidad) bajo
`data/raw/{source}/{season}/...` ANTES de escribirla en la base de datos
normalizada. Dos motivos concretos, no uno generico de "cachear por si
acaso":

1. Reprocesar sin volver a golpear la web -- si cambia el esquema de la
   base de datos (`schema.py`) no hace falta rescrapear nada, solo
   releer estos JSON y reinsertar.
2. Reanudar una ingesta larga -- CBB en concreto recorre ~180
   dias/temporada a 3.6s/peticion (mas de 10 minutos reales, ver
   docstring de `sports_reference.py`); si el proceso se corta a mitad,
   `ingest.py` puede saltarse los boxscores que ya tienen fichero en
   cache en vez de volver a pedirlos todos.

Estructura de carpetas (todas relativas a `data/raw/`):

    {source}/{season}/teams.json
    {source}/{season}/games.json
    {source}/{season}/players/{team_id}.json
    {source}/{season}/boxscores/{game_id}.json
    {source}/{season}/shots/{game_id}.json

`game_id`/`team_id` reales pueden traer caracteres que no son validos en
un nombre de fichero en Windows (":", "/", "?"...) -- aunque no se ha
visto ninguno con esas pintas en las ocho fuentes hasta ahora (slugs,
codigos alfanumericos, IDs numericos), `_safe_filename()` los sustituye
de forma defensiva en vez de asumir que nunca van a aparecer.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.scraper.base import BoxscoreData, GameRef, Player, PlayerStatLine, Shot, Team, TeamStatLine

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe_filename(value: str) -> str:
    return _UNSAFE_CHARS.sub("_", value)


def _season_dir(source: str, season: str, raw_dir: Path) -> Path:
    return raw_dir / source / _safe_filename(season)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)  # escritura atomica: nunca deja un .json a medias


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --- teams -------------------------------------------------------------

def teams_path(source: str, season: str, raw_dir: Path = DEFAULT_RAW_DIR) -> Path:
    return _season_dir(source, season, raw_dir) / "teams.json"


def save_teams(source: str, season: str, teams: list[Team], raw_dir: Path = DEFAULT_RAW_DIR) -> None:
    _write_json(teams_path(source, season, raw_dir), [asdict(t) for t in teams])


def load_teams(source: str, season: str, raw_dir: Path = DEFAULT_RAW_DIR) -> list[Team] | None:
    data = _read_json(teams_path(source, season, raw_dir))
    return None if data is None else [Team(**d) for d in data]


# --- players (un fichero por equipo) ------------------------------------

def players_path(source: str, season: str, team_id: str, raw_dir: Path = DEFAULT_RAW_DIR) -> Path:
    return _season_dir(source, season, raw_dir) / "players" / f"{_safe_filename(team_id)}.json"


def save_players(
    source: str, season: str, team_id: str, players: list[Player], raw_dir: Path = DEFAULT_RAW_DIR
) -> None:
    _write_json(players_path(source, season, team_id, raw_dir), [asdict(p) for p in players])


def load_players(
    source: str, season: str, team_id: str, raw_dir: Path = DEFAULT_RAW_DIR
) -> list[Player] | None:
    data = _read_json(players_path(source, season, team_id, raw_dir))
    return None if data is None else [Player(**d) for d in data]


# --- games ---------------------------------------------------------------

def games_path(source: str, season: str, raw_dir: Path = DEFAULT_RAW_DIR) -> Path:
    return _season_dir(source, season, raw_dir) / "games.json"


def save_games(source: str, season: str, games: list[GameRef], raw_dir: Path = DEFAULT_RAW_DIR) -> None:
    _write_json(games_path(source, season, raw_dir), [asdict(g) for g in games])


def load_games(source: str, season: str, raw_dir: Path = DEFAULT_RAW_DIR) -> list[GameRef] | None:
    data = _read_json(games_path(source, season, raw_dir))
    return None if data is None else [GameRef(**d) for d in data]


# --- boxscores (un fichero por partido) -----------------------------------

def boxscore_path(source: str, season: str, game_id: str, raw_dir: Path = DEFAULT_RAW_DIR) -> Path:
    return _season_dir(source, season, raw_dir) / "boxscores" / f"{_safe_filename(game_id)}.json"


def save_boxscore(
    source: str, season: str, boxscore: BoxscoreData, raw_dir: Path = DEFAULT_RAW_DIR
) -> None:
    _write_json(boxscore_path(source, season, boxscore.game_id, raw_dir), asdict(boxscore))


def load_boxscore(
    source: str, season: str, game_id: str, raw_dir: Path = DEFAULT_RAW_DIR
) -> BoxscoreData | None:
    data = _read_json(boxscore_path(source, season, game_id, raw_dir))
    if data is None:
        return None
    return BoxscoreData(
        game_id=data["game_id"],
        team_stats=[TeamStatLine(**d) for d in data["team_stats"]],
        player_stats=[PlayerStatLine(**d) for d in data["player_stats"]],
    )


def has_boxscore(source: str, season: str, game_id: str, raw_dir: Path = DEFAULT_RAW_DIR) -> bool:
    """Para que ingest.py pueda saltarse partidos ya cacheados sin
    releer/deserializar el fichero entero."""
    return boxscore_path(source, season, game_id, raw_dir).exists()


# --- shots (un fichero por partido) ---------------------------------------

def shots_path(source: str, season: str, game_id: str, raw_dir: Path = DEFAULT_RAW_DIR) -> Path:
    return _season_dir(source, season, raw_dir) / "shots" / f"{_safe_filename(game_id)}.json"


def save_shots(
    source: str, season: str, game_id: str, shots: list[Shot], raw_dir: Path = DEFAULT_RAW_DIR
) -> None:
    _write_json(shots_path(source, season, game_id, raw_dir), [asdict(s) for s in shots])


def load_shots(
    source: str, season: str, game_id: str, raw_dir: Path = DEFAULT_RAW_DIR
) -> list[Shot] | None:
    data = _read_json(shots_path(source, season, game_id, raw_dir))
    return None if data is None else [Shot(**d) for d in data]


def has_shots(source: str, season: str, game_id: str, raw_dir: Path = DEFAULT_RAW_DIR) -> bool:
    return shots_path(source, season, game_id, raw_dir).exists()
