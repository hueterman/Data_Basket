"""Capa de almacenamiento normalizado (plano, seccion 09, fase 3).

De cualquier `BasketballSource` (ver `src/scraper/base.py`) a una unica
base de datos SQLite (`data/processed/basketball.db`), pasando por una
cache en disco (`data/raw/`) que evita repetir peticiones ya hechas.

Uso tipico:

    from src.pipeline import ingest_source, get_connection

    conn = get_connection()
    summary = ingest_source(mi_fuente, season="2024", conn=conn)
    print(summary)

Ver los docstrings de `schema.py` (por que las tablas son como son),
`raw_cache.py` (por que existe la cache y como esta organizada) e
`ingest.py` (el orquestador, con el aviso de coste real para CBB y la
regla de RateLimitedError) para el detalle de cada decision.
"""

from src.pipeline.db import DEFAULT_DB_PATH, get_connection
from src.pipeline.ingest import IngestSummary, ingest_source
from src.pipeline.raw_cache import DEFAULT_RAW_DIR

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_RAW_DIR",
    "IngestSummary",
    "get_connection",
    "ingest_source",
]
