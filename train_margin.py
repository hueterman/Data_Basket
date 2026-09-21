r"""Tercer modelo (siguiente paso natural tras train.py/train_shot.py,
sesion 2026-09-22): predecir el MARGEN de victoria (home_score -
away_score), no solo quien gana. Regresion sobre exactamente el mismo
dataset y las mismas features que train.py (build_pregame_game_dataset(),
FEATURE_COLS identico -- diff_*_pre + diff_rest_days + diff_elo_pre) --
la unica diferencia real es el target (`margin`, continuo, en vez de
`home_win`, binario) y el tipo de modelo (regresion en vez de
clasificacion). Igual que train_shot.py, se deja como script separado en
vez de forzarlo dentro de train.py: comparten dataset/features pero no
argumentos de CLI ni metricas de evaluacion (MAE/RMSE/R2 no son
accuracy/log_loss/roc_auc), y mezclar los dos en un unico main()
obligaria a ramificar toda la logica de evaluacion y guardado por tipo
de modelo.

POR QUE (verificado, no adivinado, ver docstring de features_pregame.py
"AÑADIDO 2026-09-22" en `margin`): el margen ya se usa DENTRO de ELO
(multiplicador de margen de victoria, ver _replay_elo()) y dentro del
prior de tiro no lo toca en absoluto -- pero como TARGET explicito nunca
se habia modelado. Aporta una señal mas rica que el binario: dos
partidos con home_win=1 pueden ser un +25 y un +1, informacion que el
clasificador de train.py descarta por completo. Casos de uso futuros
(spread, ranking de dominancia de un equipo) necesitan este numero, no
solo la probabilidad de victoria.

MODELOS:
- Baseline: predice la media de `margin` observada en train (equivale a
  "ventaja de local en puntos", el analogo en regresion del baseline de
  train.py) -- ni siquiera mira las features del partido concreto.
- Ridge (regresion lineal regularizada, no OLS simple): varias features
  de FEATURE_COLS estan correlacionadas entre si de verdad (diff_elo_pre
  y diff_win_pre, correlacion 0.887, ver bloque de ELO en
  features_pregame.py) -- OLS sin regularizar es inestable con
  colinealidad real; Ridge (L2) la absorbe sin descartar ninguna
  feature.
- XGBoost (XGBRegressor, hiperparametros identicos a train.py: dataset
  del mismo tamaño, mismo riesgo de sobreajuste con un arbol muy
  profundo).

METRICAS: MAE/RMSE (error en PUNTOS, unidad interpretable directamente)
y R2 (varianza explicada). Se añade ademas "sign_accuracy" -- el % de
partidos donde el SIGNO del margen predicho coincide con el signo real
(equivalente a "acierta quien gana" mirado desde la regresion) -- no
para comparar directamente con el accuracy de train.py (bases de
comparacion distintas, mismo split cronologico pero no necesariamente
el mismo subconjunto exacto de partidos si algun min_prior_games
difiere), sino como comprobacion de cordura: un modelo de margen que
tuviera peor sign_accuracy que el clasificador de train.py en el mismo
periodo estaria mal, no simplemente "distinto".

Uso:
    cd D:\Proyectos\Data_Basket
    python train_margin.py
    python train_margin.py --min-prior-games 8 --test-frac 0.25
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.features_pregame import ROLLING_STAT_COLS, build_pregame_game_dataset
from src.pipeline import get_connection

# Identico a train.py a proposito -- mismo dataset/features, solo cambia
# el target y el tipo de modelo (ver docstring del modulo). Redefinido
# aqui en vez de importado de train.py (que no esta pensado para
# importarse, es un script con su propio main()/argparse, mismo criterio
# que ya sigue src/predict.py con su propia copia de FEATURE_COLS).
FEATURE_COLS = [f"diff_{c}_pre" for c in ROLLING_STAT_COLS] + ["diff_rest_days", "diff_elo_pre"]
MODELS_DIR = Path(__file__).resolve().parent / "models"


def chronological_split(df: pd.DataFrame, test_frac: float, group_col: str = "source"):
    """Identica a la de train.py -- ver su docstring (reparto temporal
    por fuente, nunca aleatorio, para no dejar "ver el futuro" del mismo
    equipo en train)."""
    train_parts, test_parts = [], []
    for _, g in df.groupby(group_col):
        g = g.sort_values("date")
        n_test = max(1, round(len(g) * test_frac))
        train_parts.append(g.iloc[:-n_test])
        test_parts.append(g.iloc[-n_test:])
    train_df = pd.concat(train_parts).sort_values("date").reset_index(drop=True)
    test_df = pd.concat(test_parts).sort_values("date").reset_index(drop=True)
    return train_df, test_df


def evaluate(name: str, y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    metrics = {
        "mae": mean_absolute_error(y_true, y_pred),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": r2_score(y_true, y_pred),
        "sign_accuracy": float(np.mean(np.sign(y_pred) == np.sign(y_true))),
    }
    print(f"  {name:22s} mae={metrics['mae']:.2f}  rmse={metrics['rmse']:.2f}  "
          f"r2={metrics['r2']:.3f}  sign_accuracy={metrics['sign_accuracy']:.3f}")
    return metrics


def evaluate_by_source(test_df: pd.DataFrame, y_pred: np.ndarray) -> None:
    """Desglose por fuente -- mismo motivo que train.py: NBA (temporada
    larga) y Euroliga/EuroCup (temporada corta) no tienen el mismo
    error esperable, un unico MAE agregado no representa a ninguna."""
    tmp = test_df[["source", "margin"]].copy()
    tmp["pred"] = y_pred
    print("  desglose por fuente (n, margen medio real, MAE, R2):")
    for src, sub in tmp.groupby("source"):
        mae = mean_absolute_error(sub["margin"], sub["pred"])
        r2 = r2_score(sub["margin"], sub["pred"]) if sub["margin"].nunique() > 1 else float("nan")
        print(f"    {src:12s} n={len(sub):4d}  margen_medio={sub['margin'].mean():6.2f}  "
              f"mae={mae:.2f}  r2={r2:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-prior-games", type=int, default=5,
                         help="Partidos previos minimos por equipo para entrar en el dataset (default 5).")
    parser.add_argument("--test-frac", type=float, default=0.2,
                         help="Fraccion (por fuente, cronologica) reservada para test (default 0.2).")
    args = parser.parse_args()

    conn = get_connection()
    print(f"Construyendo dataset pre-partido (min_prior_games={args.min_prior_games})...")
    games = build_pregame_game_dataset(conn, min_prior_games=args.min_prior_games)
    conn.close()

    if games.empty:
        print("  (vacio -- no hay suficientes partidos con historial previo todavia)")
        return

    # Comprobacion de cordura: margin y home_win deben ser consistentes
    # entre si (mismo partido, dos targets calculados por separado, ver
    # docstring de build_pregame_game_dataset()) -- un desacuerdo aqui
    # apuntaria a un bug real de datos, no a una metrica rara del
    # modelo.
    mismatch = ((games["margin"] > 0) != (games["home_win"] == 1)) & (games["margin"] != 0)
    if mismatch.any():
        raise ValueError(
            f"{mismatch.sum()} partidos con margin y home_win inconsistentes -- "
            f"revisar antes de continuar (ejemplos: "
            f"{games.loc[mismatch, ['source', 'game_id', 'margin', 'home_win']].head(5).to_dict('records')})"
        )

    n_before = len(games)
    games = games.dropna(subset=FEATURE_COLS + ["margin"]).reset_index(drop=True)
    if len(games) < n_before:
        print(f"  aviso: {n_before - len(games)} partidos descartados por NaN en features/target")

    print(f"  {len(games)} partidos utilizables tras el filtro de historial minimo")
    print("  por fuente:", games.groupby("source").size().to_dict())

    train_df, test_df = chronological_split(games, args.test_frac)
    print(f"\nReparto cronologico: {len(train_df)} train / {len(test_df)} test")
    print("  train por fuente:", train_df.groupby("source").size().to_dict())
    print("  test  por fuente:", test_df.groupby("source").size().to_dict())

    X_train, y_train = train_df[FEATURE_COLS], train_df["margin"].to_numpy()
    X_test, y_test = test_df[FEATURE_COLS], test_df["margin"].to_numpy()

    print(f"\nMargen medio (puntos, +local/-visitante) -- train: {y_train.mean():.2f}  test: {y_test.mean():.2f}")

    print("\nEvaluando modelos (mae / rmse / r2 / sign_accuracy):")

    # 1) Baseline: predice la media de margin en train, sin mirar el
    # partido concreto -- cualquier modelo que no supere esto no esta
    # aportando nada.
    mean_margin = float(y_train.mean())
    baseline_pred = np.full(len(y_test), mean_margin)
    metrics_baseline = evaluate("Baseline (margen medio)", y_test, baseline_pred)

    # 2) Ridge -- ver docstring del modulo (colinealidad real entre
    # diff_elo_pre y diff_win_pre, entre otras).
    ridge = Pipeline([
        ("scaler", StandardScaler()),
        ("reg", Ridge(alpha=1.0)),
    ])
    ridge.fit(X_train, y_train)
    pred_ridge = ridge.predict(X_test)
    metrics_ridge = evaluate("Ridge", y_test, pred_ridge)

    # 3) XGBoost -- mismos hiperparametros conservadores que train.py
    # (dataset del mismo tamaño, mismo riesgo de sobreajuste).
    xgb = XGBRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        eval_metric="rmse", random_state=42,
    )
    xgb.fit(X_train, y_train)
    pred_xgb = xgb.predict(X_test)
    metrics_xgb = evaluate("XGBoost", y_test, pred_xgb)

    print("\nRidge -- desglose por liga (el numero agregado no representa a ninguna en concreto):")
    evaluate_by_source(test_df, pred_ridge)

    print("\nCoeficientes de Ridge (positivo = favorece al local, escala estandarizada):")
    coefs = pd.Series(ridge.named_steps["reg"].coef_, index=FEATURE_COLS).sort_values()
    print(coefs.round(3).to_string())

    print("\nImportancia de features en XGBoost (gain):")
    importances = pd.Series(xgb.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    print(importances.round(3).to_string())

    metrics_by_source = {}
    for src, sub in test_df.groupby("source"):
        pred_sub = ridge.predict(sub[FEATURE_COLS])
        metrics_by_source[src] = {
            "n": int(len(sub)),
            "margin_mean": float(sub["margin"].mean()),
            "mae": float(mean_absolute_error(sub["margin"], pred_sub)),
        }

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(ridge, MODELS_DIR / "game_margin_ridge.joblib")
    joblib.dump(xgb, MODELS_DIR / "game_margin_xgb.joblib")
    metadata = {
        "target": "margin",
        "feature_cols": FEATURE_COLS,
        "min_prior_games": args.min_prior_games,
        "test_frac": args.test_frac,
        "n_train": len(train_df),
        "n_test": len(test_df),
        "metrics": {
            "baseline": metrics_baseline,
            "ridge": metrics_ridge,
            "xgboost": metrics_xgb,
        },
        "ridge_metrics_by_source": metrics_by_source,
    }
    with open(MODELS_DIR / "game_margin_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"\nModelos guardados en {MODELS_DIR}/ "
          f"(game_margin_ridge.joblib, game_margin_xgb.joblib, game_margin_metadata.json)")


if __name__ == "__main__":
    main()
