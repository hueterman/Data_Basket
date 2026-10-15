"""Construye/revisa la tabla `team_club` (identificador estable de club).

    python scripts/build_clubs.py                 # recalcula para todas las fuentes
    python scripts/build_clubs.py --source feb    # solo una fuente
    python scripts/build_clubs.py --show feb      # clubes agrupados de una fuente
    python scripts/build_clubs.py --suggest       # posibles cambios de nombre sin unir

Se recalcula sola al final de cada ingesta; este script sirve para forzarlo
(p. ej. tras fusionar bases de datos o editar src/pipeline/club_aliases.csv) y
para revisar candidatos a alias. Ver src/pipeline/clubs.py.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline import clubs  # noqa: E402
from src.pipeline.repository import team_seasons  # noqa: E402

# Palabras demasiado genericas para sugerir que dos nombres son el mismo club.
_STOP = {"CLUB", "BALONCESTO", "BASKET", "BASKETBALL", "BASQUET", "BASKET", "CB", "CD", "SAD",
         "DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "FEMENINO", "FEMENI", "B", "A", "SAN", "SANTA"}


def club_table(conn, source):
    """[(club_id|None, nombre, [team_ids], [temporadas])] ordenado por nombre."""
    mapping = clubs.load_map(conn)
    seasons = team_seasons(conn)
    rows = conn.execute("SELECT team_id, name FROM teams WHERE source=?", (source,)).fetchall()
    grouped: dict[str, dict] = {}
    for tid, name in rows:
        tid = str(tid)
        key = mapping.get((source, tid), f"id:{tid}")
        g = grouped.setdefault(key, {"name": name, "ids": [], "seasons": set()})
        g["ids"].append(tid)
        g["seasons"] |= {s.strip() for s in seasons.get((source, tid), [])}
    return sorted(((k, g["name"], g["ids"], sorted(g["seasons"])) for k, g in grouped.items()),
                  key=lambda r: r[1])


def _years(seasons):
    out = set()
    for s in seasons:
        s = str(s).strip()
        if s[:4].isdigit():
            out.add(int(s[:4]))
    return out


def suggest(conn, source):
    """Pares de clubes que parecen ser el mismo con otro nombre: comparten una palabra
    distintiva, NUNCA coinciden en una temporada y alguna temporada de uno es
    CONSECUTIVA a una del otro (un club que cambia de patrocinador sigue jugando al año
    siguiente). Mucho menos ruido que comparar todos contra todos."""
    table = club_table(conn, source)
    toks = [{t for t in clubs.normalize_name(n).split() if len(t) >= 4 and t not in _STOP}
            for _k, n, _i, _s in table]
    yrs = [_years(t[3]) for t in table]
    out = []
    for i in range(len(table)):
        for j in range(i + 1, len(table)):
            if not (toks[i] & toks[j]) or (yrs[i] & yrs[j]):
                continue
            if any(abs(a - b) == 1 for a in yrs[i] for b in yrs[j]):
                out.append((table[i], table[j], sorted(toks[i] & toks[j])))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "processed" / "basketball.db")
    ap.add_argument("--source")
    ap.add_argument("--show", metavar="SOURCE")
    ap.add_argument("--suggest", action="store_true")
    ap.add_argument("--draft", type=Path, metavar="FICHERO",
                    help="con --suggest: escribe las lineas candidatas (formato club_aliases.csv) "
                         "en FICHERO para revisarlas/editarlas antes de usarlas")
    args = ap.parse_args(argv)
    try:  # que `| head` no deje un traceback
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    conn = sqlite3.connect(str(args.db), timeout=120)

    if args.show:
        for key, name, ids, seasons in club_table(conn, args.show):
            if len(ids) > 1:
                print(f"{name:45s} {len(ids)} ids  temporadas: {', '.join(seasons)}")
        return 0
    if args.suggest:
        sources = [args.source] if args.source else [
            r[0] for r in conn.execute("SELECT DISTINCT source FROM team_club ORDER BY 1")]
        draft = ["source,name,same_as"]
        for src in sources:
            pairs = suggest(conn, src)
            print(f"\n== {src}: {len(pairs)} posibles cambios de nombre ==")
            for a, b, shared in pairs:
                print(f"  {a[1]} [{', '.join(a[3])}]  <->  {b[1]} [{', '.join(b[3])}]   (comun: {', '.join(shared)})")
                print(f"     -> {src},{b[1]},{a[1]}")
                draft.append(f"{src},{b[1]},{a[1]}")
        if args.draft:
            args.draft.write_text("\n".join(draft) + "\n", encoding="utf-8")
            print(f"\nBorrador escrito en {args.draft} ({len(draft) - 1} lineas). Revisalo y copia las "
                  f"buenas a src/pipeline/club_aliases.csv.")
        else:
            print("\nPara unir un par, copia la linea '->' en src/pipeline/club_aliases.csv y recalcula.")
        return 0

    n = clubs.rebuild(conn, [args.source] if args.source else None)
    print(f"team_club: {n} ids agrupados en clubes de mas de un id.")
    for src, k, m in conn.execute(
            "SELECT source, COUNT(DISTINCT club_id), COUNT(*) FROM team_club GROUP BY 1 ORDER BY 1"):
        print(f"  {src:12s} {k:4d} clubes  <- {m} ids")
    return 0


if __name__ == "__main__":
    sys.exit(main())
