r"""Calcula el primer conjunto de features para train.py (Fase 5 del
plano) sobre la base de datos real del proyecto y las guarda como CSV
en data/processed/features/ -- util para inspeccionar el resultado sin
abrir un notebook, y como punto de partida directo para train.py
(pd.read_csv en vez de tener que reconstruirlas).

Por defecto SOLO usa TRAINABLE_SOURCES (ver src/scraper/base.py) --
WNBA y CBB quedan fuera aunque tengan datos en la base de datos, porque
sus terminos de uso prohiben entrenar modelos de IA con ellos (plano,
seccion 10). Esto no es opcional: build_features.py no acepta --sources
para saltarselo a proposito.

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\build_features.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features import build_shot_features, build_team_game_features, zone_fg_pct
from src.pipeline import get_connection


def main() -> None:
    conn = get_connection()
    out_dir = Path(__file__).resolve().parent.parent / "data" / "processed" / "features"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Calculando features equipo-partido (ritmo + cuatro factores)...")
    tgf = build_team_game_features(conn)
    if tgf.empty:
        print("  (vacio -- no hay partidos de fuentes entrenables en la base de datos)")
    else:
        tgf.to_csv(out_dir / "team_game_features.csv", index=False)
        print(f"  {len(tgf):,} filas -> {out_dir / 'team_game_features.csv'}")
        print(tgf.groupby("source")[["pace_40", "ortg", "drtg", "efg_pct", "tov_pct", "orb_pct", "ft_rate"]]
              .mean().round(3).to_string())

    print("\nCalculando features de tiro (coordenadas normalizadas + zona)...")
    sf = build_shot_features(conn)
    if sf.empty:
        print("  (vacio -- no hay tiros de fuentes entrenables en la base de datos)")
    else:
        sf.to_csv(out_dir / "shot_features.csv", index=False)
        print(f"  {len(sf):,} filas -> {out_dir / 'shot_features.csv'}")
        zp = zone_fg_pct(sf, by=["source"])
        print(zp.pivot(index="zone", columns="source", values="fg_pct").round(3).to_string())

    conn.close()


if __name__ == "__main__":
    main()
