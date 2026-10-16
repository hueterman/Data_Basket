r"""Prueba manual de get_shots() de ACB contra la API real (api2.acb.com).

IMPORTANTE: ejecutar en tu propio equipo, en una terminal normal (el bridge
del chat no tiene salida de red). Requiere ACB_API_KEY en .env o en el entorno.

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\test_acb_shots.py            # partido 105393
    python scripts\test_acb_shots.py 104464     # otro matchId

Hace 3 cosas:
 1. Descarga el JSON crudo y lo guarda en data/cache/acb_probe_<id>.json
    (gitignored) para poder inspeccionar la estructura real.
 2. Llama a AcbSource.get_shots() y resume lo que sale.
 3. Comprueba la hipotesis de unidades (mm, x=0 en la linea de fondo): los
    tiros mas cercanos al aro deben concentrarse cerca de x ~ 1575, y ~ 0,
    y los triples deben caer a ~6750 del aro.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper import acb  # noqa: E402
from src.scraper.acb import AcbSource, AcbSourceError  # noqa: E402

HOOP_X_MM = 1575  # centro del aro a 1.575 m de la linea de fondo (FIBA)


def main() -> None:
    match_id = sys.argv[1] if len(sys.argv) > 1 else "105393"
    game_id = f"local-vs-visitante-{match_id}"

    # 1) JSON crudo
    headers = {
        "Accept": "*/*", "Origin": "https://live.acb.com",
        "Referer": "https://live.acb.com/", "X-APIKEY": acb._load_api_key(),
        "User-Agent": acb._HEADERS["User-Agent"],
    }
    resp = requests.get(f"{acb._API_BASE}{acb._SHOTS_PATH}", headers=headers,
                        params={"matchId": match_id}, timeout=30)
    print(f"HTTP {resp.status_code}, {len(resp.content)} bytes")
    resp.raise_for_status()
    payload = resp.json()
    out = Path(__file__).resolve().parent.parent / "data" / "cache" / f"acb_probe_{match_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"JSON crudo guardado en {out}")
    print("Claves de primer nivel:", sorted(payload.keys()))
    pts = payload.get("shotPoints") or []
    print(f"shotPoints: {len(pts)} eventos")
    if pts:
        print("Claves de un shotPoint:", sorted(pts[0].keys()))
        print("Ejemplo:", pts[0])
        print("playType:", dict(Counter(p.get("playType") for p in pts)))

    # 2) get_shots()
    try:
        shots = AcbSource().get_shots(game_id)
    except AcbSourceError as exc:
        print(f"\nget_shots FALLO: {exc}")
        print("-> pega aqui esta salida y el JSON de data/cache para ajustar el parseo.")
        return
    made = sum(s.made for s in shots)
    print(f"\nget_shots: {len(shots)} tiros de campo, {made} anotados "
          f"({100 * made / max(len(shots), 1):.1f}%)")
    print("Por equipo:", dict(Counter(s.team_id for s in shots)))
    print("Por periodo:", dict(sorted(Counter(s.period for s in shots).items(), key=lambda kv: (kv[0] is None, kv[0]))))
    xs = [s.loc_x for s in shots]
    ys = [s.loc_y for s in shots]
    print(f"Rango x: {min(xs):.0f}..{max(xs):.0f}   Rango y: {min(ys):.0f}..{max(ys):.0f}")

    # 3) hipotesis de unidades. Cada tiro puede ir a cualquiera de los dos aros
    # (x ~ 1575 o x ~ 28000-1575), asi que se mide contra el mas cercano.
    def dist_mm(s) -> float:
        dx = min(abs(s.loc_x - HOOP_X_MM), abs(s.loc_x - (28000 - HOOP_X_MM)))
        return math.hypot(dx, s.loc_y)

    threes = sorted(dist_mm(s) for s in shots if s.shot_type == "3")
    twos = sorted(dist_mm(s) for s in shots if s.shot_type == "2")
    if threes and twos:
        med3, med2 = threes[len(threes) // 2], twos[len(twos) // 2]
        print(f"Distancia mediana al aro: T2 = {med2:.0f}, T3 = {med3:.0f}")
        print("  Esperado si son mm: T3 ~ 6800-7500 y T2 ~ 2000-4500.")
        print("  Si T3 sale ~ 7 o ~ 680 las unidades no son mm.")
    if min(xs) >= 0 and max(xs) > 14000:
        print("AVISO: x llega a > 14000, asi que hay tiros en coordenadas de pista "
              "completa (dos aros). Para dibujar media pista habra que reflejar.")


if __name__ == "__main__":
    main()
