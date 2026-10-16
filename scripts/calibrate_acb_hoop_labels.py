r"""Calibra el aro de ACB usando la etiqueta triple/doble que ya trae cada tiro.

Para cada x de aro candidata (y = 0), predice "triple" si la distancia al aro es
>= 6750 mm y mide el acierto contra playType (94/98 = triple, 93/97 = doble).
Solo se usan tiros de la zona donde dobles y triples se mezclan: 2500 < posX < 10000
y |posY| < 5500 (sin esquinas, sin bajo canasta, sin tiros de fondo de pista).
Usa tambien dobles, asi que no depende de ajustar el radio del arco.

    python scripts\calibrate_acb_hoop_labels.py                # 105370-105394
    python scripts\calibrate_acb_hoop_labels.py 105393 105386
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.scraper import acb  # noqa: E402

ARC_MM = 6750
CANDIDATES = range(-500, 2501, 25)


def usable(points: list[dict]) -> list[tuple[float, float, bool]]:
    out = []
    for p in points:
        if p.get("playType") in (93, 97, 94, 98) and 2500 < p["posX"] < 10000 and abs(p["posY"]) < 5500:
            out.append((p["posX"], p["posY"], p["playType"] in (94, 98)))
    return out


def accuracy(shots: list[tuple[float, float, bool]], hx: float) -> float:
    ok = sum((math.hypot(x - hx, y) >= ARC_MM) == is3 for x, y, is3 in shots)
    return ok / len(shots)


def best(shots):
    accs = {hx: accuracy(shots, hx) for hx in CANDIDATES}
    top = max(accs.values())
    plateau = [hx for hx, a in accs.items() if a >= top - 0.005]  # dentro de 0.5 pp del maximo
    return min(plateau), max(plateau), top, accs


def main() -> None:
    ids = sys.argv[1:] or [str(i) for i in range(105370, 105395)]
    headers = {"Accept": "*/*", "Origin": "https://live.acb.com",
               "Referer": "https://live.acb.com/", "X-APIKEY": acb._load_api_key(),
               "User-Agent": acb._HEADERS["User-Agent"]}
    pool: list[tuple[float, float, bool]] = []
    print(f"{'partido':>8} {'tiros':>6} {'hx rango':>13} {'acierto':>8} {'@1575':>7} {'@1075':>7}")
    for mid in ids:
        try:
            r = requests.get(f"{acb._API_BASE}{acb._SHOTS_PATH}", headers=headers,
                             params={"matchId": mid}, timeout=30)
            r.raise_for_status()
            shots = usable(r.json().get("shotPoints") or [])
        except requests.RequestException as exc:
            print(f"{mid:>8} omitido ({exc})")
            continue
        pool += shots
        if len(shots) >= 25:
            lo, hi, top, accs = best(shots)
            print(f"{mid:>8} {len(shots):>6} {lo:>6}-{hi:<6} {top:>8.1%} "
                  f"{accs[1575]:>7.1%} {accs[1075]:>7.1%}")
        else:
            print(f"{mid:>8} {len(shots):>6}   (pocos tiros)")
        time.sleep(2)

    if len(pool) < 200:
        print(f"\nSolo {len(pool)} tiros: insuficiente.")
        return
    lo, hi, top, accs = best(pool)
    print(f"\nGLOBAL: {len(pool)} tiros ({sum(s[2] for s in pool)} triples) -> aro en x entre {lo} y {hi} mm, "
          f"acierto maximo {top:.1%}")
    for hx in (0, 500, 775, 1075, 1575):
        print(f"   acierto con aro en {hx:>4}: {accs[hx]:.1%}")


if __name__ == "__main__":
    main()
