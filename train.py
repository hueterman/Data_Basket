r"""Primer modelo de train.py: predecir el RESULTADO del partido (gana el
local si). Segundo modelo (probabilidad de acierto de tiro, P(made))
YA ENTREGADO como script separado: train_shot.py (raiz del proyecto,
mismo patron -- reparto cronologico, baseline/regresion logistica/
XGBoost, guardado en models/ -- pero target y features de TIRO, no de
partido, ver su propio docstring).

Usa exclusivamente features PRE-partido (src/features_pregame.py):
medias de temporada de cada equipo hasta ANTES del partido a predecir,
nunca estadisticas del propio partido -- ver el docstring de
features_pregame.py para el porque (evitar fuga de datos).

Reparto train/test CRONOLOGICO, no aleatorio: para cada fuente, el
ultimo `--test-frac` de sus partidos (por fecha) va a test. Un reparto
aleatorio dejaria "ver el futuro" del mismo equipo en el set de
entrenamiento (partidos de octubre y de marzo mezclados al azar) y
sobreestimaria la precision real -- un reparto cronologico simula mejor
la situacion real de prediccion (solo se conoce el pasado del equipo).

Se entrenan y comparan tres cosas:
1. Baseline "ventaja de local": predice que gana el local con la
   probabilidad de victoria local observada en el propio train set --
   referencia minima que cualquier modelo real deberia superar.
2. Regresion logistica (interpretable, coeficientes = peso de cada
   feature).
3. XGBoost (no lineal, suele capturar mejor las interacciones).

Uso:
    cd D:\Proyectos\Data_Basket
    python train.py
    python train.py --min-prior-games 8 --test-frac 0.25
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.features_pregame import ROLLING_STAT_COLS, build_pregame_game_dataset
from src.pipeline import get_connection

FEATURE_COLS = [f"diff_{c}_pre" for c in ROLLING_STAT_COLS] + ["diff_rest_days"]
MODELS_DIR = Path(__file__).resolve().parent / "models"


def chronological_split(df: pd.DataFrame, test_frac: float, group_col: str = "source"):
    """Reparto temporal por fuente: para cada `group_col`, ordena por
    fecha y separa el ultimo `test_frac` en test. Evita mezclar
    principio/final de temporada entre train y test (ver docstring del
    modulo)."""
    train_parts, test_parts = [], []
    for _, g in df.groupby(group_col):
        g = g.sort_values("date")
        n_test = max(1, round(len(g) * test_frac))
        train_parts.append(g.iloc[:-n_test])
        test_parts.append(g.iloc[-n_test:])
    train_df = pd.concat(train_parts).sort_values("date").reset_index(drop=True)
    test_df = pd.concat(test_parts).sort_values("date").reset_index(drop=True)
    return train_df, test_df


def evaluate(name: str, y_true: np.ndarray, y_prob: np.ndarray) -> dict:
    y_pred = (y_prob >= 0.5).astype(int)
    metrics = {
        "accuracy": accuracy_score(y_true, y_pred),
        "log_loss": log_loss(y_true, y_prob, labels=[0, 1]),
        "roc_auc": roc_auc_score(y_true, y_prob) if len(set(y_true)) > 1 else float("nan"),
    }
    print(f"  {name:22s} accuracy={metrics['accuracy']:.3f}  "
          f"log_loss={metrics['log_loss']:.3f}  roc_auc={metrics['roc_auc']:.3f}")
    return metrics


def evaluate_by_source(test_df: pd.DataFrame, y_prob: np.ndarray) -> None:
    """Desglose por fuente -- el numero agregado esconde que NBA (una
    temporada larga, 82 partidos/equipo) tiene medias pre-partido mucho
    mas estables que Euroliga/EuroCup (34 partidos/equipo), y por tanto
    se predice con mas precision. Util para no venderle al usuario un
    unico accuracy que no representa a ninguna liga en particular."""
    tmp = test_df[["source", "home_win"]].copy()
    tmp["prob"] = y_prob
    print("  desglose por fuente (n, % victoria local en test, accuracy, roc_auc):")
    for src, sub in tmp.groupby("source"):
        pred = (sub["prob"] >= 0.5).astype(int)
        acc = accuracy_score(sub["home_win"], pred)
        auc = roc_auc_score(sub["home_win"], sub["prob"]) if sub["home_win"].nunique() > 1 else float("nan")
        print(f"    {src:12s} n={len(sub):4d}  home_win={sub['home_win'].mean():.3f}  "
              f"accuracy={acc:.3f}  roc_auc={auc:.3f}")


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

    # Comprobacion de cordura: home_win debe ser consistente con las
    # columnas de las que viene (no deberia poder fallar nunca, pero un
    # assert barato aqui detecta un bug real de merge antes que una
    # metrica rara).
    assert games["home_win"].isin([0, 1]).all()

    n_before = len(games)
    games = games.dropna(subset=FEATURE_COLS + ["home_win"]).reset_index(drop=True)
    if len(games) < n_before:
        print(f"  aviso: {n_before - len(games)} partidos descartados por NaN en features/target")

    print(f"  {len(games)} partidos utilizables tras el filtro de historial minimo")
    print("  por fuente:", games.groupby("source").size().to_dict())

    train_df, test_df = chronological_split(games, args.test_frac)
    print(f"\nReparto cronologico: {len(train_df)} train / {len(test_df)} test")
    print("  train por fuente:", train_df.groupby("source").size().to_dict())
    print("  test  por fuente:", test_df.groupby("source").size().to_dict())

    X_train, y_train = train_df[FEATURE_COLS], train_df["home_win"].to_numpy()
    X_test, y_test = test_df[FEATURE_COLS], test_df["home_win"].to_numpy()

    print(f"\n% victorias locales -- train: {y_train.mean():.3f}  test: {y_test.mean():.3f}")

    print("\nEvaluando modelos (accuracy / log_loss / roc_auc):")

    # 1) Baseline: probabilidad constante = % de victorias locales visto
    # en train -- ni siquiera mira las features del partido concreto.
    # Cualquier modelo que no supere esto no esta aportando nada.
    home_rate = float(y_train.mean())
    baseline_prob = np.full(len(y_test), home_rate)
    metrics_baseline = evaluate("Baseline (ventaja local)", y_test, baseline_prob)

    # 2) Regresion logistica
    logreg = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000)),
    ])
    logreg.fit(X_train, y_train)
    prob_logreg = logreg.predict_proba(X_test)[:, 1]
    metrics_logreg = evaluate("Regresion logistica", y_test, prob_logreg)

    # 3) XGBoost -- hiperparametros conservadores a proposito: el
    # dataset es todavia pequeño (una temporada por liga), un arbol
    # profundo con muchos estimadores memorizaria ruido en vez de
    # generalizar.
    xgb = XGBClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        eval_metric="logloss", random_state=42,
    )
    xgb.fit(X_train, y_train)
    prob_xgb = xgb.predict_proba(X_test)[:, 1]
    metrics_xgb = evaluate("XGBoost", y_test, prob_xgb)

    print("\nRegresion logistica -- desglose por liga (el numero agregado no representa a ninguna en concreto):")
    evaluate_by_source(test_df, prob_logreg)

    print("\nCoeficientes de la regresion logistica (positivo = favorece al local):")
    coefs = pd.Series(logreg.named_steps["clf"].coef_[0], index=FEATURE_COLS).sort_values()
    print(coefs.round(3).to_string())

    print("\nImportancia de features en XGBoost (gain):")
    importances = pd.Series(xgb.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
    print(importances.round(3).to_string())

    metrics_by_source = {}
    for src, sub in test_df.groupby("source"):
        prob_sub = logreg.predict_proba(sub[FEATURE_COLS])[:, 1]
        metrics_by_source[src] = {
            "n": int(len(sub)),
            "home_win_rate": float(sub["home_win"].mean()),
            "accuracy": float(accuracy_score(sub["home_win"], (prob_sub >= 0.5).astype(int))),
        }

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(logreg, MODELS_DIR / "game_outcome_logreg.joblib")
    joblib.dump(xgb, MODELS_DIR / "game_outcome_xgb.joblib")
    metadata = {
        "target": "home_win",
        "feature_cols": FEATURE_COLS,
        "min_prior_games": args.min_prior_games,
        "test_frac": args.test_frac,
        "n_train": len(train_df),
        "n_test": len(test_df),
        "metrics": {
            "baseline": metrics_baseline,
            "logreg": metrics_logreg,
            "xgboost": metrics_xgb,
        },
        "logreg_metrics_by_source": metrics_by_source,
    }
    with open(MODELS_DIR / "game_outcome_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"\nModelos guardados en {MODELS_DIR}/ "
          f"(game_outcome_logreg.joblib, game_outcome_xgb.joblib, game_outcome_metadata.json)")


if __name__ == "__main__":
    main()
