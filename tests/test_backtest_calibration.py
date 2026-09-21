"""bucket_by_days_since_cutoff()/calibration_metrics()/run_backtest() en
backtest_calibration.py (AÑADIDO 2026-09-21).

run_backtest() se testea con un dataset SINTETICO (no basketball.db
real) construido a mano con la misma forma que devuelve
build_pregame_game_dataset() -- suficiente para verificar la mecanica
(reparto train/eval por corte, bucketing, que las 3 claves del modelo
-- baseline/logreg/xgboost -- aparezcan, que los conteos por bucket
sumen el total) sin depender de que exista basketball.db en este
entorno (los tests con modelos/datos reales viven aparte, en
tests/test_predict_game_outcome.py estilo `real_predict_mod`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest_calibration import (
    FEATURE_COLS,
    FINE_BUCKET_LABELS,
    SEASON_BUCKET_EDGES_DAYS,
    SEASON_BUCKET_LABELS,
    bucket_by_days_since_cutoff,
    calibration_metrics,
    run_backtest,
)


def test_bucket_by_days_since_cutoff_assigns_expected_labels():
    cutoff = pd.Timestamp("2023-01-01", tz="UTC")
    dates = pd.Series(pd.to_datetime([
        "2023-01-01",  # dia 0 -> primer bucket
        "2023-06-01",  # ~151 dias -> segundo bucket (365-730 fuera; aqui uso edges custom)
        "2025-06-01",  # bastante mas de un año
    ], utc=True))
    edges = [0, 365, 730, float("inf")]
    labels = ["a", "b", "c"]
    result = bucket_by_days_since_cutoff(dates, cutoff, edges, labels)
    assert list(result) == ["a", "a", "c"]


def test_calibration_metrics_perfect_separation():
    y_true = np.array([0, 0, 1, 1])
    y_prob = np.array([0.1, 0.1, 0.9, 0.9])
    m = calibration_metrics(y_true, y_prob, n_bins=5)

    assert m["n"] == 4
    assert m["accuracy"] == pytest.approx(1.0)
    assert m["roc_auc"] == pytest.approx(1.0)
    assert m["brier"] == pytest.approx(0.01)  # (0.1^2 * 2 + 0.1^2 * 2) / 4
    assert m["ece"] == pytest.approx(0.1)  # ver docstring del test module


def test_calibration_metrics_single_class_roc_auc_is_nan():
    # roc_auc no esta definido con una sola clase en y_true -- mismo
    # criterio que evaluate() en train.py (ternario con float("nan")).
    m = calibration_metrics(np.array([1, 1, 1]), np.array([0.6, 0.7, 0.8]))
    assert np.isnan(m["roc_auc"])


def _synthetic_pregame_dataset(n_per_bucket: int = 60, seed: int = 0) -> pd.DataFrame:
    """Construye un dataset con la misma forma que build_pregame_game_
    dataset() (mismas columnas que usa run_backtest()), con partidos
    repartidos a mano en train (antes del corte) y en los 3 primeros
    buckets de 'por_temporada' tras el corte, y una relacion real
    (aunque simple) entre `diff_win_pre` y `home_win` para que
    logreg/xgboost tengan algo que aprender."""
    rng = np.random.default_rng(seed)
    cutoff = pd.Timestamp("2023-09-21", tz="UTC")

    # 1 bloque de train (bastante antes del corte) + 3 bloques de eval,
    # uno por cada año de SEASON_BUCKET_EDGES_DAYS/LABELS.
    offsets_days = [-500] + [d + 30 for d in SEASON_BUCKET_EDGES_DAYS[:3]]  # train, año1, año2, año3
    frames = []
    for offset in offsets_days:
        date = cutoff + pd.Timedelta(days=offset)
        diff_win_pre = rng.normal(0, 1, n_per_bucket)
        other_feats = {c: rng.normal(0, 1, n_per_bucket) for c in FEATURE_COLS if c != "diff_win_pre"}
        home_win_prob = 1 / (1 + np.exp(-diff_win_pre))  # señal real y simple
        home_win = (rng.uniform(size=n_per_bucket) < home_win_prob).astype(int)
        block = pd.DataFrame({
            "source": "synthetic",
            "date_dt": date,
            "home_win": home_win,
            "diff_win_pre": diff_win_pre,
            "home_n_prior_games": rng.integers(5, 40, n_per_bucket),
            "away_n_prior_games": rng.integers(5, 40, n_per_bucket),
            **other_feats,
        })
        frames.append(block)
    return pd.concat(frames, ignore_index=True)


def test_run_backtest_synthetic_structure_and_counts():
    games = _synthetic_pregame_dataset(n_per_bucket=60)
    cutoff = pd.Timestamp("2023-09-21", tz="UTC")

    results = run_backtest(games, cutoff)

    assert results["cutoff"] == "2023-09-21"
    assert results["n_train"] == 60  # el unico bloque con offset negativo

    season = results["by_scheme"]["por_temporada"]
    assert set(season.keys()) == {"baseline", "logreg", "xgboost"}
    for model_name in ("baseline", "logreg", "xgboost"):
        by_bucket = season[model_name]["by_bucket"]
        # los 3 bloques de eval caen en año1/año2/año3 -- 60 partidos cada uno
        assert by_bucket["año 1 (0-1a)"]["n"] == 60
        assert by_bucket["año 2 (1-2a)"]["n"] == 60
        assert by_bucket["año 3 (2-3a)"]["n"] == 60
        assert season[model_name]["overall"]["n"] == 180  # suma de los 3 buckets de eval

    fine = results["by_scheme"]["fino"]
    assert set(fine.keys()) == {"baseline", "logreg", "xgboost"}
    total_fine = sum(m["n"] for m in fine["logreg"]["by_bucket"].values())
    assert total_fine == 180

    # logreg deberia distinguir mejor que el azar en un dataset con
    # señal real (diff_win_pre -> home_win_prob construida a proposito) --
    # no se pide una cifra exacta, solo que bata claramente a 0.5.
    assert season["logreg"]["overall"]["roc_auc"] > 0.6

    assert isinstance(results["logreg_by_source_and_season_bucket"], list)
    assert len(results["logreg_by_source_and_season_bucket"]) > 0


def test_run_backtest_raises_if_no_data_on_one_side_of_cutoff():
    games = _synthetic_pregame_dataset(n_per_bucket=10)
    # corte muy en el futuro -- todo el dataset cae en train, eval vacio
    with pytest.raises(ValueError):
        run_backtest(games, pd.Timestamp("2099-01-01", tz="UTC"))
