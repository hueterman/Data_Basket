"""Calentamiento de los modelos al arrancar la API (rama `perfil-rendimiento`).

POR QUE: la primera explicacion SHAP de cada tipo de modelo en un proceso
tarda de mas porque `shap` compila con numba (en este proyecto se midio
~3.5 s en un PC; en una Raspberry Pi 4 sera bastante mas). Es un coste
UNICO POR PROCESO, no por peticion -- las llamadas siguientes tardan
milisegundos. Esto lo paga el primer usuario que llame a /predict o
/predict_shot tras cada reinicio. Aqui se paga una vez, en segundo plano,
nada mas arrancar (el servidor ya responde mientras tanto).

Hace peticiones reales (las mismas que usa tests/test_api_integration.py)
contra cada modelo disponible. Nunca tumba el arranque: cualquier fallo se
registra y se sigue.
"""
from __future__ import annotations

import importlib
import time


def _team_pairs(open_conn, limit: int = 6) -> list[tuple[str, str, str]]:
    conn = open_conn()
    try:
        rows = conn.execute(
            "SELECT source, team_id FROM teams ORDER BY source, team_id"
        ).fetchall()
    finally:
        conn.close()
    by_source: dict[str, list[str]] = {}
    for r in rows:
        by_source.setdefault(r[0], []).append(r[1])
    pairs: list[tuple[str, str, str]] = []
    for source, ids in by_source.items():
        for i in range(0, len(ids) - 1, 2):
            pairs.append((source, ids[i], ids[i + 1]))
            if len(pairs) >= limit:
                return pairs
    return pairs


def _log(msg: str) -> None:
    # flush=True: bajo systemd stdout no es una terminal y Python lo guarda en
    # un bufer -- sin esto las lineas "[warmup]" no aparecerian en journalctl.
    print(msg, flush=True)


def warm_models(open_conn, log=_log) -> list[tuple[str, bool, float]]:
    """Calienta los modelos de tiro, resultado y margen. Devuelve
    [(etiqueta, ok, segundos)] -- util para tests y para el log."""
    predict = importlib.import_module("src.predict")
    results: list[tuple[str, bool, float]] = []

    def run(label, fn) -> bool:
        t0 = time.perf_counter()
        ok = True
        try:
            fn()
        except Exception as exc:  # incluye PredictionError: calentar es opcional
            ok = False
            log(f"[warmup] {label}: omitido ({type(exc).__name__}: {exc})")
        dt = time.perf_counter() - t0
        results.append((label, ok, dt))
        if ok:
            log(f"[warmup] {label}: {dt:.1f}s")
        return ok

    # --- modelos de tiro (cada tipo de explainer se compila aparte) ---
    for name in predict.available_shot_models():
        run(f"tiro/{name}", lambda n=name: predict.shot_make_probability(
            source="nba", x_m=0.3, y_m=0.5, shot_type="2", model_name=n))

    # --- modelos de resultado + margen ---
    outcome = list(predict.available_models())
    margin = list(predict.available_margin_models())
    if outcome and margin:
        combos = [(outcome[i % len(outcome)], margin[i % len(margin)])
                  for i in range(max(len(outcome), len(margin)))]
        try:
            pairs = _team_pairs(open_conn)
        except Exception as exc:
            log(f"[warmup] sin equipos para calentar partidos ({exc})")
            pairs = []

        def game_call(pair, combo):
            def _call():
                conn = open_conn()
                try:
                    predict.predict_game(
                        conn, source=pair[0], home_team_id=pair[1], away_team_id=pair[2],
                        model_name=combo[0], margin_model_name=combo[1],
                        home_rest_days=0.0, away_rest_days=0.0)
                finally:
                    conn.close()
            return _call

        # Busca un par de equipos con partidos jugados (alguno puede no tenerlos).
        good = None
        for pair in pairs:
            if run(f"partido/{combos[0][0]}+{combos[0][1]}", game_call(pair, combos[0])):
                good = pair
                break
        if good:
            for combo in combos[1:]:
                run(f"partido/{combo[0]}+{combo[1]}", game_call(good, combo))
    return results
