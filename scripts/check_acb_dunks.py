r"""Comprueba si los tiros ACB en (0,0) son siempre mates (playType 100).

Para N partidos recientes cuenta: (a) tiros de campo exactamente en (0,0) y de
que playType son; (b) mates (playType 100) y cuantos tienen coordenadas reales.

    python scripts\check_acb_dunks.py                  # partidos 105370-105394
    python scripts\check_acb_dunks.py 105393 105386
"""
from __future__ import annotations

import collections
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.scraper import acb  # noqa: E402

FG = acb._FIELD_GOAL_PLAY_TYPES


def main() -> None:
    ids = sys.argv[1:] or [str(i) for i in range(105370, 105395)]
    headers = {"Accept": "*/*", "Origin": "https://live.acb.com", "Referer": "https://live.acb.com/",
               "X-APIKEY": acb._load_api_key(), "User-Agent": acb._HEADERS["User-Agent"]}
    zero_types: collections.Counter = collections.Counter()
    dunk_pos = collections.Counter()
    dunk_made = collections.Counter()
    dunk_zone = collections.Counter()
    total_fg = games = 0
    for mid in ids:
        try:
            r = requests.get(f"{acb._API_BASE}{acb._SHOTS_PATH}", headers=headers,
                             params={"matchId": mid}, timeout=30)
            r.raise_for_status()
            pts = [p for p in (r.json().get("shotPoints") or []) if p.get("playType") in FG]
        except requests.RequestException as exc:
            print(f"{mid}: omitido ({exc})")
            continue
        games += 1
        total_fg += len(pts)
        for p in pts:
            at_zero = p["posX"] == 0 and p["posY"] == 0
            if at_zero:
                zero_types[p["playType"]] += 1
            if p["playType"] == 100:
                dunk_pos["en (0,0)" if at_zero else "con posicion real"] += 1
                dunk_made[p.get("made")] += 1
                dunk_zone[str(p.get("zone"))] += 1
        time.sleep(2)
    n_zero = sum(zero_types.values())
    print(f"\n{games} partidos, {total_fg} tiros de campo")
    print(f"En (0,0): {n_zero} ({100*n_zero/max(total_fg,1):.1f}%), por playType: {dict(zero_types)}")
    print(f"Mates (100): {sum(dunk_pos.values())} -> {dict(dunk_pos)}; made: {dict(dunk_made)}; zone: {dict(dunk_zone)}")


if __name__ == "__main__":
    main()
