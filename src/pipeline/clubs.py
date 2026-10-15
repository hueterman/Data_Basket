"""Identificador estable de CLUB (misma entidad entre temporadas).

PROBLEMA (verificado en datos reales): en FEB, Segunda FEB, Liga Femenina
(lfendesa/lfchallenge/lf2) y copaespaa, el mismo club recibe un `team_id`
DISTINTO cada temporada (p. ej. Alimerka Oviedo: 888968 en 2022, 922723 en
2023, 951497 en 2024...). Las caracteristicas pre-partido y el Elo agrupan por
(source, team_id), asi que cada temporada el club aparecia como un equipo
nuevo: sin arrastre de la temporada anterior y con el Elo reiniciado.

SOLUCION: tabla `team_club (source, team_id, club_id)` que agrupa los ids de un
mismo club, y `apply_club_ids()` que sustituye el id por el club_id al construir
las caracteristicas (src/features.py). REGLAS:
- Mismo nombre normalizado (mayusculas, sin tildes ni signos) dentro de la misma
  fuente => mismo club.
- Dos ids con el mismo nombre que COINCIDEN en alguna temporada son equipos
  distintos (p. ej. un filial): nunca se unen.
- Los cambios de nombre por patrocinio NO se detectan solos: se declaran en
  `club_aliases.csv` (revisar con `python scripts/build_clubs.py --suggest`).
- Solo se guardan los clubes con MAS DE UN id. El resto queda sin fila y su
  team_id se queda tal cual: fuentes con ids estables (ACB, NBA, Euroleague...)
  no cambian en absoluto, y sin tabla todo se comporta como antes.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import unicodedata
from pathlib import Path

from src.pipeline.repository import team_seasons

ALIASES_PATH = Path(__file__).with_name("club_aliases.csv")
CLUB_PREFIX = "club:"


def normalize_name(name: str | None) -> str:
    """"ACTEL FORÇA LLEIDA" -> "ACTEL FORCA LLEIDA" (mayusculas, sin tildes ni signos)."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", text)).strip()


def _slug(norm: str) -> str:
    return CLUB_PREFIX + norm.lower().replace(" ", "-")


def load_aliases(path: Path = ALIASES_PATH) -> dict[tuple[str, str], str]:
    """{(source, nombre_normalizado): nombre_normalizado_del_club}. `source` "*" vale
    para cualquier fuente. Lineas vacias o que empiezan por # se ignoran."""
    out: dict[tuple[str, str], str] = {}
    if not Path(path).is_file():
        return out
    with open(path, encoding="utf-8", newline="") as fh:
        lines = (ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#"))
        for row in csv.DictReader(lines):
            src, name, same_as = (row.get("source") or "").strip(), row.get("name"), row.get("same_as")
            if src and name and same_as:
                out[(src, normalize_name(name))] = normalize_name(same_as)
    return out


def _alias_key(source: str, norm: str, aliases: dict[tuple[str, str], str]) -> str:
    seen = set()
    while True:  # cadenas A->B->C; seen evita ciclos
        nxt = aliases.get((source, norm)) or aliases.get(("*", norm))
        if not nxt or nxt in seen or nxt == norm:
            return norm
        seen.add(norm)
        norm = nxt


def compute_mapping(
    teams: list[tuple[str, str, str]],
    seasons: dict[tuple[str, str], list[str]],
    aliases: dict[tuple[str, str], str] | None = None,
) -> dict[tuple[str, str], str]:
    """teams = [(source, team_id, name)]; seasons = {(source, team_id): [temporadas]}.
    Devuelve {(source, team_id): club_id} SOLO para clubes con 2 o mas ids."""
    aliases = aliases or {}
    groups: dict[tuple[str, str], list[str]] = {}
    for source, team_id, name in teams:
        norm = normalize_name(name)
        if not norm:
            continue
        groups.setdefault((source, _alias_key(source, norm, aliases)), []).append(str(team_id))

    def season_set(source: str, tid: str) -> set[str]:
        return {s.strip() for s in seasons.get((source, tid), [])}

    mapping: dict[tuple[str, str], str] = {}
    for (source, key), ids in groups.items():
        # Reparto determinista en "clubes": un id entra en el primer club con el que
        # NO coincida en ninguna temporada; si coincide con todos, abre uno nuevo.
        clubs: list[tuple[list[str], set[str]]] = []
        for tid in sorted(ids, key=lambda t: (min(season_set(source, t) or {"~"}), t)):
            ss = season_set(source, tid)
            for members, used in clubs:
                if not (used & ss):
                    members.append(tid)
                    used |= ss
                    break
            else:
                clubs.append(([tid], set(ss)))
        for n, (members, _used) in enumerate(clubs):
            if len(members) < 2:
                continue
            club_id = _slug(key) + ("" if n == 0 else f"#{n + 1}")
            for tid in members:
                mapping[(source, tid)] = club_id
    return mapping


# --- tabla en la base de datos ----------------------------------------------

def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS team_club ("
        " source TEXT NOT NULL, team_id TEXT NOT NULL, club_id TEXT NOT NULL,"
        " PRIMARY KEY (source, team_id))"
    )


def rebuild(conn: sqlite3.Connection, sources: list[str] | None = None,
            aliases_path: Path = ALIASES_PATH) -> int:
    """Recalcula `team_club` (para `sources`, o todas). Devuelve filas guardadas."""
    ensure_table(conn)
    teams = [(s, str(t), n) for s, t, n in conn.execute("SELECT source, team_id, name FROM teams")]
    if sources is not None:
        teams = [t for t in teams if t[0] in set(sources)]
    mapping = compute_mapping(teams, team_seasons(conn), load_aliases(aliases_path))
    scope = sorted({t[0] for t in teams})
    with conn:
        if scope:
            conn.executemany("DELETE FROM team_club WHERE source = ?", [(s,) for s in scope])
        conn.executemany(
            "INSERT INTO team_club (source, team_id, club_id) VALUES (?, ?, ?)",
            [(s, t, c) for (s, t), c in sorted(mapping.items())],
        )
    return len(mapping)


def load_map(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """{(source, team_id): club_id}; vacio si la tabla no existe todavia."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_club'"
    ).fetchone()
    if not has:
        return {}
    return {(s, str(t)): c for s, t, c in conn.execute("SELECT source, team_id, club_id FROM team_club")}


def club_of(conn: sqlite3.Connection, source: str, team_id: str) -> str:
    """club_id del equipo, o su propio team_id si no pertenece a ningun club agrupado."""
    return load_map(conn).get((source, str(team_id)), team_id)


def apply_club_ids(conn: sqlite3.Connection, df, cols=("team_id", "opp_id")):
    """Copia de `df` (con columna `source`) con `cols` sustituidas por su club_id."""
    mapping = load_map(conn)
    if not mapping or df.empty:
        return df
    out = df.copy()
    for col in cols:
        if col in out.columns:
            out[col] = [mapping.get((s, str(t)), t) for s, t in zip(out["source"], out[col])]
    return out
