r"""Backtest de calibracion TEMPORAL del modelo de resultado de partido
(train.py) -- responde a "¿el modelo acierta/calibra igual de bien en
partidos recientes que en partidos mas alejados de cuando se entreno?",
que es la pregunta real detras de "¿hace falta reentrenar mas a
menudo?".

NO es lo mismo que mirar el `game_outcome_metadata.json` que ya guarda
train.py: ese metadata evalua el modelo ACTUAL (entrenado con datos
hasta hoy) sobre su propio 20% de test mas reciente por fuente -- un
unico numero, sin desglose temporal.

Y OJO -- esto es la razon de ser de este script, no un detalle menor:
evaluar el modelo ACTUAL (entrenado con datos hasta hoy) sobre partidos
de "hace 3 años" seria FUGA DE DATOS, no un backtest real -- esos
partidos de hace 3 años ya estan DENTRO de su set de entrenamiento (el
reparto cronologico de train.py solo reserva el ULTIMO 20% para test,
así que el 80% mas antiguo, incluidos los partidos de hace 3 años, el
modelo ya los ha visto). Compararlos así solo mediría overfitting, no
staleness real.

Lo que este script hace en su lugar (metodologia "modelo congelado"):
1. Elige una fecha de corte `--cutoff-date` (por defecto, 3 años antes
   de hoy).
2. Entrena LogisticRegression/XGBoost SOLO con partidos ANTERIORES al
   corte -- simulando "un modelo que se entreno una vez en esa fecha y
   nunca se ha vuelto a reentrenar desde entonces".
3. Evalua ESE modelo congelado sobre los partidos POSTERIORES al corte
   (nunca vistos en el entrenamiento -- sin fuga), agrupados en dos
   esquemas de bucket distintos (ver mas abajo).
4. Si las metricas de calibracion (Brier score, ECE) y de acierto
   (accuracy/log_loss/roc_auc) se degradan segun crece el bucket, eso
   es evidencia real de que el modelo se queda desfasado con el tiempo
   y conviene reentrenar mas a menudo que "cuando me acuerde"; si se
   mantienen planas, el ritmo de reentrenamiento actual (manual, sin
   calendario fijo) no esta costando precision.

DOS esquemas de bucket, A PROPOSITO, no uno (verificado en datos reales
antes de fijar esto, no adivinado -- la primera version de este script
solo tenia el esquema "fino" y parecia mostrar que el modelo predice
PEOR justo despues de entrenarse, lo cual no tenia sentido; resulto ser
un artefacto, no señal real):
- `por_temporada` (el que SI contesta la pregunta de antigüedad): un
  bucket por cada año completo desde el corte (año 1, año 2, año 3...).
  Como `--cutoff-date` cae cerca del inicio de la temporada de casi
  todas las ligas (fin de septiembre), cada bucket de 365 dias cubre
  una temporada COMPLETA de esa liga -- inicio, mitad y final
  mezclados por igual en todos los buckets. Este es el que hay que
  mirar para decidir si hace falta reentrenar mas a menudo.
- `fino` (0-3m/3-6m/6-12m/1-2a/2a+, la resolucion que se pediria a
  primera vista): estos buckets SI degradan en el bucket "0-3m", pero
  por un motivo distinto a la antigüedad del modelo -- 0-3m tras un
  corte de finales de septiembre son, para casi todas las ligas,
  literalmente los primeros partidos de la temporada (media real de
  ~9-10 partidos previos por equipo en los datos de este proyecto,
  frente a ~25-45 en el resto de buckets, que ya mezclan temporada
  completa) -- las medias PRE-partido de train.py con pocos partidos
  jugados son ruidosas *aunque `min_prior_games` ya filtre el minimo*.
  Se deja este esquema en el output (con la columna `avg_n_prior_games`
  al lado para que el efecto se vea, no se esconda) como diagnostico de
  "el modelo predice peor al principio de cada temporada" -- una
  observacion real e independiente, pero NO evidencia de que haga
  falta reentrenar mas a menudo.

Alcance -- SOLO el modelo de resultado de partido (train.py), no
train_shot.py: la fuerza de un equipo cambia con el tiempo (fichajes,
bajas, forma de la temporada), así que tiene sentido preguntarse si el
modelo se desfasa; la probabilidad de que entre un tiro desde una
zona/distancia dada es sobre todo geometria del baloncesto, mucho mas
estable año a año -- si se quiere el mismo backtest para train_shot.py,
la mecanica (bucket_by_days_since_cutoff/calibration_metrics de este
modulo) es reutilizable tal cual, cambiando solo el dataset y el
Pipeline de entrada.

Uso:
    cd D:\Proyectos\Data_Basket
    python backtest_calibration.py
    python backtest_calibration.py --cutoff-date 2022-09-21 --min-prior-games 8
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.features_pregame import ROLLING_STAT_COLS, build_pregame_game_dataset
from src.pipeline import get_connection

FEATURE_COLS = [f"diff_{c}_pre" for c in ROLLING_STAT_COLS] + ["diff_rest_days"]
MODELS_DIR = Path(__file__).resolve().parent / "models"

# Esquema "fino" -- diagnostico de fase de temporada, NO de antigüedad
# real (ver docstring del modulo).
FINE_BUCKET_EDGES_DAYS = [0, 91, 182, 365, 730, float("inf")]
FINE_BUCKET_LABELS = ["0-3m", "3-6m", "6-12m", "1-2a", "2a+"]

# Esquema "por_temporada" -- el que SI responde "¿hace falta reentrenar
# mas a menudo?": un año completo (365 dias) por bucket, desde el
# corte, así que cada uno mezcla inicio/mitad/final de temporada por
# igual en vez de aislar solo el arranque.
SEASON_BUCKET_EDGES_DAYS = [0, 365, 730, 1095, 1460, float("inf")]
SEASON_BUCKET_LABELS = ["año 1 (0-1a)", "año 2 (1-2a)", "año 3 (2-3a)", "año 4 (3-4a)", "4a+"]

BUCKET_SCHEMES: dict[str, tuple[list[float], list[str]]] = {
    "por_temporada": (SEASON_BUCKET_EDGES_DAYS, SEASON_BUCKET_LABELS),
    "fino": (FINE_BUCKET_EDGES_DAYS, FINE_BUCKET_LABELS),
}


def bucket_by_days_since_cutoff(
    dates: pd.Series, cutoff: pd.Timestamp,
    edges_days: list[float], labels: list[str],
) -> pd.Series:
    """Bucket categorico por cuantos dias han pasado desde `cutoff` hasta
    cada fecha de `dates` (`dates` ya se asume posterior a `cutoff` --
    dias negativos quedarian fuera de todos los bins, NaN). `edges_days`
    tiene que tener un elemento mas que `labels` (limites de bin, estilo
    `pd.cut`)."""
    days_since = (dates - cutoff).dt.total_seconds() / 86400.0
    return pd.cut(days_since, bins=edges_days, labels=labels, right=False)


def calibration_metrics(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 5) -> dict:
    """Metricas de acierto (accuracy/log_loss/roc_auc, mismas que
    evaluate() en train.py) mas dos de CALIBRACION real:
    - brier: error cuadratico medio entre probabilidad predicha y
      resultado real (0=perfecto). A diferencia de accuracy/roc_auc (que
      solo miran el ORDEN de las probabilidades), Brier SI penaliza una
      probabilidad mal calibrada aunque el orden de acierto/fallo sea
      correcto -- es la metrica que de verdad contesta "¿cuando el
      modelo dice 70%, gana de verdad ~70% de las veces?".
    - ece (Expected Calibration Error): las predicciones se agrupan en
      `n_bins` tramos de probabilidad (0-20%, 20-40%...); por tramo se
      compara la probabilidad media predicha con el % de aciertos REAL
      en ese tramo, y se promedia la diferencia absoluta ponderando por
      el tamaño de cada tramo. Complementa a Brier con una lectura mas
      directa ("en los partidos donde dije ~70%, ¿de verdad gano un
      ~70%?") y es mas facil de explicar en clase que Brier a secas.
    """
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    y_pred = (y_prob >= 0.5).astype(int)

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_ids = np.clip(np.digitize(y_prob, bins[1:-1]), 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        mask = bin_ids == b
        if not mask.any():
            continue
        ece += (mask.sum() / len(y_prob)) * abs(y_true[mask].mean() - y_prob[mask].mean())

    return {
        "n": int(len(y_true)),
        "home_win_rate": float(y_true.mean()),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "log_loss": float(log_loss(y_true, y_prob, labels=[0, 1])) if len(y_true) else float("nan"),
        "roc_auc": float(roc_auc_score(y_true, y_prob)) if len(set(y_true)) > 1 else float("nan"),
        "brier": float(brier_score_loss(y_true, y_prob)),
        "ece": float(ece),
    }


def _fit_models(train_df: pd.DataFrame) -> dict:
    """Mismos hiperparametros que train.py -- el objetivo es comparar
    calibracion en el tiempo, no reabrir la eleccion de hiperparametros."""
    X_train, y_train = train_df[FEATURE_COLS], train_df["home_win"].to_numpy()

    logreg = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000)),
    ])
    logreg.fit(X_train, y_train)

    xgb = XGBClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        eval_metric="logloss", random_state=42,
    )
    xgb.fit(X_train, y_train)

    return {"logreg": logreg, "xgboost": xgb}


def _metrics_for_scheme(
    eval_df: pd.DataFrame, models: dict, home_rate: float,
    edges_days: list[float], labels: list[str],
) -> dict:
    bucket = bucket_by_days_since_cutoff(eval_df["date_dt"], eval_df.attrs["cutoff"], edges_days, labels)
    out: dict = {}
    for model_name, model in {**models, "baseline": None}.items():
        by_bucket = {}
        for label in labels:
            sub = eval_df[bucket == label]
            if sub.empty:
                continue
            prob = (np.full(len(sub), home_rate) if model is None
                    else model.predict_proba(sub[FEATURE_COLS])[:, 1])
            m = calibration_metrics(sub["home_win"].to_numpy(), prob)
            m["avg_n_prior_games"] = float(((sub["home_n_prior_games"] + sub["away_n_prior_games"]) / 2).mean())
            by_bucket[label] = m
        prob_all = (np.full(len(eval_df), home_rate) if model is None
                    else model.predict_proba(eval_df[FEATURE_COLS])[:, 1])
        out[model_name] = {
            "overall": calibration_metrics(eval_df["home_win"].to_numpy(), prob_all),
            "by_bucket": by_bucket,
        }
    return out


def run_backtest(games: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    """Nucleo del backtest, separado de main() para poder testearlo con
    datos sinteticos sin tocar la base de datos real. `games` ya debe
    traer `date_dt` (datetime UTC) y las columnas de FEATURE_COLS/
    home_win/home_n_prior_games/away_n_prior_games sin NaN (mismo
    filtro que train.py)."""
    train_df = games[games["date_dt"] < cutoff]
    eval_df = games[games["date_dt"] >= cutoff].copy()
    eval_df.attrs["cutoff"] = cutoff

    if train_df.empty or eval_df.empty:
        raise ValueError(
            f"corte {cutoff.date()} deja train={len(train_df)}/eval={len(eval_df)} partidos -- "
            "elige un --cutoff-date con partidos a ambos lados."
        )

    models = _fit_models(train_df)
    home_rate = float(train_df["home_win"].mean())

    results: dict = {"cutoff": str(cutoff.date()), "n_train": int(len(train_df)), "by_scheme": {}}
    for scheme_name, (edges_days, labels) in BUCKET_SCHEMES.items():
        results["by_scheme"][scheme_name] = _metrics_for_scheme(eval_df, models, home_rate, edges_days, labels)

    # Desglose por fuente x bucket de temporada (logreg) -- mas
    # granular, se guarda en el JSON pero no se imprime por consola.
    bucket_season = bucket_by_days_since_cutoff(eval_df["date_dt"], cutoff, SEASON_BUCKET_EDGES_DAYS, SEASON_BUCKET_LABELS)
    by_source_bucket = []
    for (source, label), sub in eval_df.groupby(["source", bucket_season], observed=True):
        if sub.empty:
            continue
        prob = models["logreg"].predict_proba(sub[FEATURE_COLS])[:, 1]
        m = calibration_metrics(sub["home_win"].to_numpy(), prob)
        by_source_bucket.append({"source": source, "bucket": label, **m})
    results["logreg_by_source_and_season_bucket"] = by_source_bucket

    return results


def _print_table(scheme_results: dict, labels: list[str]) -> None:
    for model_name in ("baseline", "logreg", "xgboost"):
        print(f"\n  {model_name}:")
        header = (f"    {'bucket':14s} {'n':>5s} {'home_win':>9s} {'accuracy':>9s} {'log_loss':>9s} "
                  f"{'roc_auc':>8s} {'brier':>7s} {'ece':>7s} {'avg_n_prior':>11s}")
        print(header)
        for label in labels:
            m = scheme_results[model_name]["by_bucket"].get(label)
            if m is None:
                continue
            print(f"    {label:14s} {m['n']:5d} {m['home_win_rate']:9.3f} {m['accuracy']:9.3f} "
                  f"{m['log_loss']:9.3f} {m['roc_auc']:8.3f} {m['brier']:7.3f} {m['ece']:7.3f} "
                  f"{m['avg_n_prior_games']:11.1f}")
        m = scheme_results[model_name]["overall"]
        print(f"    {'TODO':14s} {m['n']:5d} {m['home_win_rate']:9.3f} {m['accuracy']:9.3f} "
              f"{m['log_loss']:9.3f} {m['roc_auc']:8.3f} {m['brier']:7.3f} {m['ece']:7.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-prior-games", type=int, default=5,
                         help="Igual que train.py (default 5).")
    parser.add_argument("--cutoff-date", type=str, default=None,
                         help="Fecha de corte YYYY-MM-DD (entrena solo con partidos anteriores). "
                              "Por defecto, hoy menos --cutoff-years-ago.")
    parser.add_argument("--cutoff-years-ago", type=float, default=3.0,
                         help="Si no se pasa --cutoff-date, cuantos años antes de hoy poner el corte (default 3.0).")
    args = parser.parse_args()

    conn = get_connection()
    print(f"Construyendo dataset pre-partido (min_prior_games={args.min_prior_games})...")
    games = build_pregame_game_dataset(conn, min_prior_games=args.min_prior_games)
    conn.close()

    if games.empty:
        print("  (vacio -- no hay suficientes partidos con historial previo todavia)")
        return

    n_before = len(games)
    games = games.dropna(subset=FEATURE_COLS + ["home_win"]).reset_index(drop=True)
    if len(games) < n_before:
        print(f"  aviso: {n_before - len(games)} partidos descartados por NaN en features/target")

    games["date_dt"] = pd.to_datetime(games["date"], utc=True, format="mixed", errors="coerce")
    n_bad_dates = games["date_dt"].isna().sum()
    if n_bad_dates:
        print(f"  aviso: {n_bad_dates} partidos descartados por fecha ilegible")
        games = games.dropna(subset=["date_dt"]).reset_index(drop=True)

    if args.cutoff_date:
        cutoff = pd.Timestamp(args.cutoff_date, tz="UTC")
    else:
        cutoff = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=365.25 * args.cutoff_years_ago)

    print(f"\nCorte de entrenamiento: {cutoff.date()} "
          f"(rango real de datos: {games['date_dt'].min().date()} .. {games['date_dt'].max().date()})")

    results = run_backtest(games, cutoff)
    print(f"\nEntrenado con {results['n_train']} partidos anteriores al corte "
          f"(logreg/xgboost, mismos hiperparametros que train.py).")

    print("\n=== Esquema 'por_temporada' (antigüedad real del modelo congelado -- el que responde a la pregunta) ===")
    _print_table(results["by_scheme"]["por_temporada"], SEASON_BUCKET_LABELS)

    print("\n=== Esquema 'fino' (diagnostico de fase de temporada, NO de antigüedad -- ver avg_n_prior_games) ===")
    _print_table(results["by_scheme"]["fino"], FINE_BUCKET_LABELS)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MODELS_DIR / "backtest_calibration_metadata.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "min_prior_games": args.min_prior_games,
            "feature_cols": FEATURE_COLS,
            "season_bucket_edges_days": SEASON_BUCKET_EDGES_DAYS,
            "season_bucket_labels": SEASON_BUCKET_LABELS,
            "fine_bucket_edges_days": FINE_BUCKET_EDGES_DAYS,
            "fine_bucket_labels": FINE_BUCKET_LABELS,
            **results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nDetalle completo (incluye desglose por fuente x bucket de temporada) guardado en {out_path}")


if __name__ == "__main__":
    main()
