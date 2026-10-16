"""Evalua los modelos de tiro guardados separando, dentro del test, los tiros
ACB con posicion conocida de los mates imputados (pos_known=False).

Los mates imputados son todos aciertos pegados al aro (casos faciles), asi que
las metricas de ACB con ellos dentro salen algo optimistas. Aqui se ve el
rendimiento sobre tiros con posicion real y cuanto subestima el modelo a los
mates imputados."""
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import train_shot as ts  # noqa: E402


def build_test_set(test_frac):
    conn = ts.get_connection()
    shots = ts.build_shot_features(conn, sources=ts.SHOT_TRAINABLE_SOURCES)
    shots["sin_pos"] = ~shots["pos_known"].astype(bool)

    # Misma imputacion que train_shot.main()
    unknown = shots["sin_pos"]
    for src in shots.loc[unknown, "source"].unique():
        m_src = shots["source"] == src
        ref = shots[m_src & ~unknown & (shots["zone"] == "aro")]
        miss = m_src & unknown
        for col in ("dist_m", "angle_deg"):
            shots.loc[miss, col] = ref[col].median()

    src_list = sorted(shots["source"].unique())
    placeholders = ",".join("?" for _ in src_list)
    games_dates = pd.read_sql(
        f"SELECT source, game_id, date FROM games WHERE played = 1 AND source IN ({placeholders})",
        conn, params=src_list,
    )
    conn.close()
    games_dates["date"] = pd.to_datetime(games_dates["date"], utc=True, format="mixed", errors="coerce")

    shots = ts.add_shot_priors(shots, games_dates, k_shrink=ts.SHOT_PRIOR_K_SHRINK)
    shots = shots.dropna(subset=ts.FEATURE_COLS + ["made"]).reset_index(drop=True)
    shots["made"] = shots["made"].astype(int)
    _, test_df = ts.chronological_split_by_game(shots, games_dates, test_frac)

    if "sin_pos" not in test_df.columns:
        test_df = test_df.copy()
        test_df["sin_pos"] = (test_df["source"] == "acb") & (test_df["loc_x"] == 0) & (test_df["loc_y"] == 0)
    return test_df


def metrics(y, p):
    two_classes = y.nunique() == 2
    return {
        "n": len(y),
        "fg_real": y.mean(),
        "prob_media": p.mean(),
        "accuracy": accuracy_score(y, p >= 0.5),
        "log_loss": log_loss(y, p, labels=[0, 1]),
        "roc_auc": roc_auc_score(y, p) if two_classes else np.nan,
    }


def main():
    meta_path = ts.MODELS_DIR / "shot_make_metadata.json"
    test_frac = json.loads(meta_path.read_text(encoding="utf-8")).get("test_frac", 0.2)
    test_df = build_test_set(test_frac)
    print(f"Test reconstruido: {len(test_df)} tiros (test_frac={test_frac})")

    acb = test_df["source"] == "acb"
    groups = {
        "acb (todos)": acb,
        "acb posicion conocida": acb & ~test_df["sin_pos"],
        "acb mates imputados": acb & test_df["sin_pos"],
        "resto de fuentes": ~acb,
    }
    X = test_df[ts.FEATURE_COLS]
    y = test_df["made"]

    for name, fname in (("XGBoost", "shot_make_xgb.joblib"), ("Regresion logistica", "shot_make_logreg.joblib")):
        model = joblib.load(ts.MODELS_DIR / fname)
        p = pd.Series(model.predict_proba(X)[:, 1], index=test_df.index)
        rows = {g: metrics(y[m], p[m]) for g, m in groups.items() if m.any()}
        print(f"\n=== {name} ===")
        print(pd.DataFrame(rows).T.round(3).to_string())

        aro = acb & (test_df["zone"] == "aro")
        rows_aro = {
            "acb aro (todos)": metrics(y[aro], p[aro]),
            "acb aro posicion conocida": metrics(y[aro & ~test_df["sin_pos"]], p[aro & ~test_df["sin_pos"]]),
        }
        print(f"-- ACB, solo zona aro ({name}):")
        print(pd.DataFrame(rows_aro).T[["n", "fg_real", "prob_media"]].round(3).to_string())


if __name__ == "__main__":
    main()
