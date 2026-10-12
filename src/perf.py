"""Ajustes de rendimiento configurables (rama `perfil-rendimiento`).

Un unico sitio para todo lo que se puede afinar segun la maquina donde
corra la app, sin tocar el comportamiento por defecto en un PC normal.

PERFIL (variable de entorno `DATA_BASKET_PROFILE`):
    auto     (por defecto) -> "pi" si se detecta una Raspberry Pi, si no "default".
    pi       -> pensado para Raspberry Pi 4 (4 nucleos, 2-8 GB de RAM, SSD por USB).
    default  -> no cambia nada respecto al comportamiento original.

Cada valor se puede forzar por separado con su propia variable de entorno
(tienen prioridad sobre el perfil):
    DB_CACHE_KB          cache de paginas de SQLite por conexion, en KiB
    DB_MMAP_MB           memoria mapeada de SQLite, en MiB (0 = desactivada)
    DB_SYNCHRONOUS       OFF | NORMAL | FULL (NORMAL es seguro con WAL)
    WAITRESS_THREADS     hilos del servidor waitress
    SHOT_CACHE           1 | 0  -> cache en disco de los PNG de shot charts
    MODEL_NTHREAD        hilos de XGBoost/BLAS al predecir (0 = no tocar)
    WARMUP               1 | 0  -> calentar los modelos (SHAP/numba) al arrancar la API
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHOT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "shots"


def _is_raspberry_pi() -> bool:
    try:
        return "raspberry pi" in Path("/proc/device-tree/model").read_text(errors="ignore").lower()
    except OSError:
        return False


_PROFILES: dict[str, dict] = {
    "default": {
        "db_cache_kb": None,   # None = no tocar el valor por defecto de SQLite
        "db_mmap_mb": 0,
        "db_synchronous": None,
        "waitress_threads": 4,
        "shot_cache": False,
        "model_nthread": 0,
        "warmup": False,
    },
    "pi": {
        "db_cache_kb": 16384,   # 16 MiB por conexion (la de SQLite por defecto es ~2 MiB)
        "db_mmap_mb": 256,      # la BD pesa ~300 MB: lecturas casi sin copiar
        "db_synchronous": "NORMAL",
        "waitress_threads": 4,  # 4 nucleos; la ingesta corre en otro proceso
        "shot_cache": True,     # matplotlib en la Pi es lo mas caro por peticion
        "model_nthread": 2,     # deja libres los otros 2 nucleos a la ingesta
        "warmup": True,         # paga la compilacion de SHAP una vez, al arrancar
    },
}


def _env_int(name: str, fallback):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def active_profile() -> str:
    requested = os.environ.get("DATA_BASKET_PROFILE", "auto").strip().lower()
    if requested == "auto":
        return "pi" if _is_raspberry_pi() else "default"
    return requested if requested in _PROFILES else "default"


def settings() -> dict:
    """Valores efectivos (perfil + sobrescrituras por variable de entorno).
    Se evalua en cada llamada: barato y facilita los tests."""
    base = dict(_PROFILES[active_profile()])
    base["db_cache_kb"] = _env_int("DB_CACHE_KB", base["db_cache_kb"])
    base["db_mmap_mb"] = _env_int("DB_MMAP_MB", base["db_mmap_mb"])
    sync = os.environ.get("DB_SYNCHRONOUS", "").strip().upper()
    if sync in {"OFF", "NORMAL", "FULL"}:
        base["db_synchronous"] = sync
    base["waitress_threads"] = max(1, _env_int("WAITRESS_THREADS", base["waitress_threads"]))
    shot = os.environ.get("SHOT_CACHE")
    if shot is not None and shot.strip() in {"0", "1"}:
        base["shot_cache"] = shot.strip() == "1"
    base["model_nthread"] = max(0, _env_int("MODEL_NTHREAD", base["model_nthread"]))
    warm = os.environ.get("WARMUP")
    if warm is not None and warm.strip() in {"0", "1"}:
        base["warmup"] = warm.strip() == "1"
    return base


def apply_sqlite_pragmas(conn) -> None:
    """Se llama desde src.pipeline.db.get_connection() tras fijar WAL."""
    cfg = settings()
    if cfg["db_synchronous"]:
        conn.execute(f"PRAGMA synchronous={cfg['db_synchronous']}")
    if cfg["db_cache_kb"]:
        conn.execute(f"PRAGMA cache_size=-{int(cfg['db_cache_kb'])}")  # negativo = KiB
    if cfg["db_mmap_mb"]:
        conn.execute(f"PRAGMA mmap_size={int(cfg['db_mmap_mb']) * 1024 * 1024}")


def apply_thread_limits() -> None:
    """Limita los hilos de BLAS/OpenMP (los usan numpy, scikit-learn y
    XGBoost al predecir). Llamar ANTES de importar esas librerias."""
    n = settings()["model_nthread"]
    if n:
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ.setdefault(var, str(n))
