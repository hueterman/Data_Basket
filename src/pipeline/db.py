"""Conexion a la base de datos normalizada (SQLite).

Un unico fichero, `data/processed/basketball.db` por defecto (ruta
relativa a la raiz del proyecto) -- ya anticipado en `.gitignore`
(`data/processed/`, `*.db`), no se versiona: se reconstruye reingestando
desde `data/raw/` (ver `raw_cache.py`) o volviendo a scrapear.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src import perf
from src.pipeline.schema import init_db

# Raiz del proyecto = dos niveles por encima de este fichero
# (src/pipeline/db.py -> src/pipeline -> src -> raiz).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "processed" / "basketball.db"


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Abre (creando si hace falta) la base de datos normalizada.

    NO activa `PRAGMA foreign_keys=ON` a proposito -- ver el docstring de
    `schema.py` sobre por que las claves foraneas aqui son documentacion,
    no una restriccion dura: datos scrapeados de webs reales tienen casos
    raros que no deberian tirar abajo una ingesta por lo demas valida.

    BUG REAL reportado por el usuario (2026-09-20): `sqlite3.OperationalError:
    database is locked` al navegar la app (app/app.py, cada peticion abre su
    propia conexion con esta misma funcion) mientras una ingesta
    (scripts/ingest_data.py o ingest_historical.py, lanzada desde el panel
    "Ingestas" o por linea de comandos, OTRO proceso, su propia conexion via
    esta misma funcion) estaba escribiendo en el MISMO fichero al mismo
    tiempo -- uso normal de la app (esta pensada para poder mirar datos
    mientras se ingieren mas), no un caso raro. Causa: el modo de journal
    por defecto de SQLite (`DELETE`/rollback-journal) hace que un lector
    pueda quedar bloqueado por un escritor en plena transaccion, y
    `sqlite3.connect()` sin `timeout` explicito solo reintenta 5s antes de
    lanzar la excepcion -- con una ingesta larga (cientos de partidos,
    varios `commit()` reales por partido en repository.py) la ventana de
    colision es real, no un caso extremo. FIX: activar `PRAGMA
    journal_mode=WAL` (lectores y escritor no se bloquean entre si; solo
    dos escritores a la vez siguen serializandose, caso mucho mas raro
    aqui) y subir el `busy_timeout` a 30s como red de seguridad para esa
    minoria de casos -- estandar para el patron "una app de solo lectura +
    un proceso de escritura aparte sobre el mismo fichero SQLite". El modo
    WAL queda grabado en el propio fichero (persiste entre conexiones y
    procesos una vez activado), pero se fija en cada conexion de todas
    formas por si se apunta a un basketball.db antiguo sin WAL todavia.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    perf.apply_sqlite_pragmas(conn)  # cache/mmap/synchronous segun perfil (ver src/perf.py)
    init_db(conn)
    return conn
