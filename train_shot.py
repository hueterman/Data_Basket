r"""Segundo modelo (pendiente anotado en el docstring de train.py):
probabilidad de que un tiro entre, P(made), sobre build_shot_features()
(Fase 5, src/features.py). Sigue el mismo patron que train.py -- mismo
tipo de reparto cronologico, misma comparacion baseline/regresion
logistica/XGBoost, mismo formato de guardado en models/ -- pero como
script separado: el target es a nivel de TIRO, no de partido, con sus
propias features y su propio reparto, y forzarlos dentro de un unico
main() obligaria a compartir argumentos de CLI que no tienen nada que
ver entre si.

FEATURES, todas conocidas ANTES de que el tiro se resuelva (no hay
fuga de datos posible aqui -- a diferencia del modelo de resultado de
partido, que sí tuvo que evitar estadisticas del propio partido, ver
src/features_pregame.py -- distancia/angulo/zona/tipo de intento son
propiedades del LANZAMIENTO, no de su resultado):
- dist_m, angle_deg: coordenadas polares ya calculadas por
  build_shot_features() (loc_x_m/loc_y_m serian redundantes con estas
  dos, se dejan fuera para no repetir la misma informacion dos veces).
- zone: agrupamiento espacial de build_shot_features() (aro/pintura_
  corta/media_larga/triple_esquina/triple_arco) -- aproximacion
  geometrica propia por anillos de distancia, documentada como tal en
  su propio docstring.
- shot_type: "2"/"3" REAL segun la fuente (accion 2FGA/3FGA original,
  no derivado de zone) -- se incluye ADEMAS de zone porque pueden
  discrepar cerca de la linea de 3 (un tiro que build_shot_features()
  clasifica como "media_larga" por la aproximacion geometrica pero que
  la fuente registro como intento de 3 real, o al reves); esa
  discrepancia es informacion real sobre el tipo de tiro, no ruido a
  eliminar.
- source: el % de acierto base difiere por liga/temporada (estilo de
  juego, nivel) -- se dejan que el modelo lo aprenda en vez de asumir
  que NBA/Euroliga/EuroCup tiran igual.

FUENTES: solo las que ya tienen coordenadas de tiro calibradas Y son
TRAINABLE (ver SHOT_COORD_CALIBRATION en src/features.py) -- a fecha
de esta sesion eso es NBA, Euroliga y EuroCup. ACB no implementa
get_shots() (sin endpoint de coordenadas conocido, ver acb.py); FEB
sin partidos jugados todavia; WNBA SI tiene tiros con coordenadas
calibradas, pero es NON_TRAINABLE y build_shot_features(trainable_
only=True, el default) ya la excluye -- nunca pasar sources=["wnba"]
aqui.

REPARTO TRAIN/TEST CRONOLOGICO POR PARTIDO, no por tiro (mismo
criterio que chronological_split() de train.py, adaptado): para cada
fuente se ordenan los PARTIDOS por fecha y el ultimo `--test-frac` va
a test -- TODOS los tiros de un partido caen juntos al mismo lado, en
vez de repartir tiros del mismo partido al azar entre train y test
(evitaria dejar que una racha o un rival concretos "se vean" parcialmente
en ambos conjuntos). `date` mezcla formatos por fuente (NBA "YYYY-MM-DD"
vs Euroliga/EuroCup ISO con hora y "Z", mismo caso ya resuelto en
features_pregame.py) -- se parsea con format="mixed".

Uso:
    cd D:\Proyectos\Data_Basket
    python train_shot.py
    python train_shot.py --test-frac 0.25
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.features import SHOT_COORD_CALIBRATION, build_shot_features, zone_fg_pct
from src.pipeline import get_connection
from src.scraper.base import TRAINABLE_SOURCES

# AMPLIACION 2026-09-20: al ampliar FebSource a 8 competiciones (ver
# feb.py/base.py) resulto que get_shots() SI esta implementado alli --
# feb/lfendesa/lfchallenge/lf2/segundafeb/tercerafeb/copaespaa ya tienen
# tiros reales en la tabla `shots` (confirmado corriendo este mismo
# script: 100k-190k tiros cada una segun la competicion), algo que el
# comentario de SHOT_COORD_CALIBRATION en features.py daba por no
# existente todavia -- ya corregido alli. Pero NADIE ha calibrado sus
# coordenadas (three_pt_arc_m/three_pt_corner_m, conversion a metros)
# con el mismo rigor que NBA/Euroliga/EuroCup/WNBA (confirmacion en vivo
# + densidad de tiros, ver notebook secciones 3/5/6) -- eso es un
# trabajo de verificacion aparte, no algo que se pueda inventar aqui sin
# violar "no adivinar". Por eso este script fija `sources` explicitamente
# a solo las fuentes YA calibradas (interseccion de SHOT_COORD_CALIBRATION
# y TRAINABLE_SOURCES) en vez de dejar que build_shot_features(conn) sin
# argumentos intente coger TODAS las entrenables con tiros -- así, el
# dia que se calibre una fuente nueva, este script la coge sola sin
# tocar codigo, y mientras tanto no revienta en bloque por culpa de
# fuentes con tiros reales pero sin calibrar todavia.
SHOT_TRAINABLE_SOURCES = sorted(set(SHOT_COORD_CALIBRATION) & TRAINABLE_SOURCES)

NUMERIC_COLS = ["dist_m", "angle_deg"]
CATEGORICAL_COLS = ["zone", "shot_type", "source"]
FEATURE_COLS = NUMERIC_COLS + CATEGORICAL_COLS
MODELS_DIR = Path(__file__).resolve().parent / "models"


def _preprocessor() -> ColumnTransformer:
    """Compartido por regresion logistica y XGBoost -- cada pipeline se
    queda con su propia instancia ya ajustada (fit) sobre train, no se
    reutiliza el objeto entre modelos."""
    return ColumnTransformer([
        ("num", StandardScaler(), NUMERIC_COLS),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_COLS),
    ])


def chronological_split_by_game(
    shots: pd.DataFrame, games_dates: pd.DataFrame, test_frac: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reparte PARTIDOS (no tiros) cronologicamente por fuente -- ver
    docstring del modulo -- y devuelve los tiros de `shots` ya
    separados en (train_df, test_df) segun a que lado cayo su
    game_id."""
    train_keys: set[tuple[str, str]] = set()
    test_keys: set[tuple[str, str]] = set()
    for source, g in games_dates.groupby("source"):
        g = g.sort_values("date")
        n_test = max(1, round(len(g) * test_frac))
        train_keys.update(zip(g["source"].iloc[:-n_test], g["game_id"].iloc[:-n_test]))
        test_keys.update(zip(g["source"].iloc[-n_test:], g["game_id"].iloc[-n_test:]))

    keys = list(zip(shots["source"], shots["game_id"]))
    is_train = pd.Series(keys, index=shots.index).isin(train_keys)
    is_test = pd.Series(keys, index=shots.index).isin(test_keys)
    return shots[is_train].reset_index(drop=True), shots[is_test].reset_index(drop=True)


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


def sanity_check_by_zone(test_df: pd.DataFrame, y_prob: np.ndarray) -> dict:
    """FG% real por zona en test vs. probabilidad MEDIA predicha por el
    modelo para esa zona -- deberian rondarse (ver rangos reales
    conocidos documentados en zone_fg_pct(), src/features.py: aro
    60-65%, triple_arco 33-38%). No es una metrica de exactitud, es una
    comprobacion de cordura: si la prediccion media de una zona se va
    muy lejos del acierto real observado en esa zona, algo esta mal en
    las features o el entrenamiento, no en el modelo en si."""
    tmp = test_df[["zone", "made"]].copy()
    tmp["pred_prob"] = y_prob
    out = tmp.groupby("zone").agg(
        intentos=("made", "size"),
        fg_pct_real=("made", "mean"),
        prob_media_predicha=("pred_prob", "mean"),
    ).round(3)
    print("\nComprobacion de cordura -- FG% real vs. probabilidad media predicha, por zona (test):")
    print(out.to_string())
    return out.reset_index().to_dict(orient="records")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test-frac", type=float, default=0.2,
                         help="Fraccion de PARTIDOS (por fuente, cronologica) reservada para test (default 0.2).")
    args = parser.parse_args()

    conn = get_connection()
    print(f"Construyendo dataset de tiros (build_shot_features(), fuentes calibradas y "
          f"entrenables: {SHOT_TRAINABLE_SOURCES})...")
    shots = build_shot_features(conn, sources=SHOT_TRAINABLE_SOURCES)
    if shots.empty:
        print("  (vacio -- no hay tiros de fuentes entrenables todavia en la base de datos)")
        conn.close()
        return

    src_list = sorted(shots["source"].unique())
    placeholders = ",".join("?" for _ in src_list)
    games_dates = pd.read_sql(
        f"SELECT source, game_id, date FROM games WHERE played = 1 AND source IN ({placeholders})",
        conn, params=src_list,
    )
    conn.close()
    # Mismo problema de formatos de fecha mixtos por fuente ya resuelto
    # en features_pregame.py (NBA "YYYY-MM-DD", sin zona horaria, vs
    # Euroliga/EuroCup ISO con hora y "Z", UTC) -- utc=True + format=
    # "mixed" (sin utc=True, pandas rechaza mezclar fechas con y sin
    # zona horaria en la misma columna: "Mixed timezones detected",
    # confirmado en este mismo script) + comprobacion explicita con
    # errors="coerce", mismo criterio "avisa y no adivina" que el resto
    # del proyecto.
    raw_dates = games_dates["date"]
    games_dates["date"] = pd.to_datetime(raw_dates, utc=True, format="mixed", errors="coerce")
    bad_dates = raw_dates.notna() & games_dates["date"].isna()
    if bad_dates.any():
        examples = games_dates.loc[bad_dates, ["source", "game_id"]].head(5).to_dict("records")
        raise ValueError(
            f"No se pudo parsear la fecha de {bad_dates.sum()} partidos -- "
            f"formato inesperado, revisar antes de continuar (ejemplos: {examples})"
        )

    n_before = len(shots)
    shots = shots.dropna(subset=FEATURE_COLS + ["made"]).reset_index(drop=True)
    if len(shots) < n_before:
        print(f"  aviso: {n_before - len(shots)} tiros descartados por NaN en features/target")
    shots["made"] = shots["made"].astype(int)

    print(f"  {len(shots)} tiros utilizables -- por fuente: {shots.groupby('source').size().to_dict()}")

    train_df, test_df = chronological_split_by_game(shots, games_dates, args.test_frac)
    print(f"\nReparto cronologico por PARTIDO: {len(train_df)} tiros train / {len(test_df)} tiros test")
    print("  train por fuente:", train_df.groupby("source").size().to_dict())
    print("  test  por fuente:", test_df.groupby("source").size().to_dict())

    X_train, y_train = train_df[FEATURE_COLS], train_df["made"].to_numpy()
    X_test, y_test = test_df[FEATURE_COLS], test_df["made"].to_numpy()

    print(f"\nFG% global -- train: {y_train.mean():.3f}  test: {y_test.mean():.3f}")

    print("\nEvaluando modelos (accuracy / log_loss / roc_auc):")

    # 1) Baseline: probabilidad constante = FG% global visto en train --
    # ni siquiera mira donde se tiro. Cualquier modelo que no supere
    # esto no esta aportando nada sobre "cualquier tiro entra X% de las
    # veces".
    fg_rate = float(y_train.mean())
    baseline_prob = np.full(len(y_test), fg_rate)
    metrics_baseline = evaluate("Baseline (FG% global)", y_test, baseline_prob)

    # 2) Regresion logistica
    logreg = Pipeline([
        ("pre", _preprocessor()),
        ("clf", LogisticRegression(max_iter=1000)),
    ])
    logreg.fit(X_train, y_train)
    prob_logreg = logreg.predict_proba(X_test)[:, 1]
    metrics_logreg = evaluate("Regresion logistica", y_test, prob_logreg)

    # 3) XGBoost -- dataset mucho mas grande que el del modelo de
    # resultado de partido (aqui una fila = un tiro, no un partido), asi
    # que se permite algo mas de profundidad/arboles que en train.py sin
    # riesgo de memorizar ruido.
    xgb = Pipeline([
        ("pre", _preprocessor()),
        ("clf", XGBClassifier(
            n_estimators=300, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            eval_metric="logloss", random_state=42,
        )),
    ])
    xgb.fit(X_train, y_train)
    prob_xgb = xgb.predict_proba(X_test)[:, 1]
    metrics_xgb = evaluate("XGBoost", y_test, prob_xgb)

    sanity = sanity_check_by_zone(test_df, prob_logreg)

    print("\nFG% real por zona y fuente, sobre TODOS los tiros utilizables (referencia, no solo test):")
    print(zone_fg_pct(shots, by=["source"]).round(3).to_string(index=False))

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(logreg, MODELS_DIR / "shot_make_logreg.joblib")
    joblib.dump(xgb, MODELS_DIR / "shot_make_xgb.joblib")
    metadata = {
        "target": "made",
        "feature_cols": FEATURE_COLS,
        "test_frac": args.test_frac,
        "n_train": len(train_df),
        "n_test": len(test_df),
        "sources": src_list,
        "metrics": {
            "baseline": metrics_baseline,
            "logreg": metrics_logreg,
            "xgboost": metrics_xgb,
        },
        "sanity_check_by_zone": sanity,
    }
    with open(MODELS_DIR / "shot_make_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"\nModelos guardados en {MODELS_DIR}/ "
          f"(shot_make_logreg.joblib, shot_make_xgb.joblib, shot_make_metadata.json)")


if __name__ == "__main__":
    main()
