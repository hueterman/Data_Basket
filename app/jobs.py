"""Gestor del job de ingesta en segundo plano para el panel /ingestas de
app.py.

Un UNICO job a la vez, a proposito: dos ingestas en paralelo escribirian
en el MISMO basketball.db desde dos procesos distintos, y src/pipeline/db.py
no activa modo WAL (ver su docstring) -- mejor no arriesgarse a un
"database is locked" a media ingesta. `start()` rechaza lanzar un
segundo job mientras el primero siga vivo.

Estado en memoria del propio proceso Flask (un dict + un Lock), no en
disco: sencillo a proposito para una app de un solo usuario local. Aviso
real: si reinicias la app (p. ej. el recargador de `debug=True` de
Flask al detectar un cambio de fichero) mientras hay una ingesta en
marcha, el subproceso de scraping sigue vivo en el sistema operativo,
pero esta app pierde la referencia y ya no lo puede parar ni ver su log
desde el panel -- habria que matarlo a mano (Administrador de tareas) o
esperar a que termine solo. No debería pasar en uso normal (no se edita
la app mientras se lanza una ingesta), pero conviene saberlo.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOG_DIR = PROJECT_ROOT / "data" / "ingest_runs"

_lock = threading.Lock()
_state: dict = {
    "proc": None,
    "log_file": None,
    "log_path": None,
    "desc": None,
    "cmd": None,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
}


class JobAlreadyRunning(Exception):
    """Ya hay una ingesta en marcha -- ver docstring del modulo."""


def _is_running_locked() -> bool:
    """Debe llamarse con _lock ya adquirido. Actualiza returncode/
    finished_at la primera vez que detecta que el proceso ha terminado."""
    proc = _state["proc"]
    if proc is None:
        return False
    if proc.poll() is None:
        return True
    if _state["returncode"] is None:
        _state["returncode"] = proc.returncode
        _state["finished_at"] = datetime.now(timezone.utc)
        log_file = _state["log_file"]
        if log_file is not None:
            try:
                log_file.close()
            except OSError:
                pass
            _state["log_file"] = None
    return False


def start(script_name: str, args: list[str], desc: str) -> Path:
    """Lanza `python -u scripts/<script_name> <args...>` en segundo
    plano, con stdout+stderr redirigidos a un fichero de log nuevo bajo
    data/ingest_runs/<timestamp>_<script>.log. Devuelve la ruta del log.

    `-u` (Python sin buffer): las lineas de progreso de ingest_source()
    ya se imprimen con flush=True, pero las de los propios scripts
    (cabeceras "===", resumen final) no -- sin -u quedarian retenidas en
    el buffer de Python hasta que el proceso terminase, y el panel
    dejaria de parecer "en vivo".

    Lanza JobAlreadyRunning si ya hay un job vivo -- ver docstring del
    modulo sobre por que no se permiten dos a la vez.
    """
    with _lock:
        if _is_running_locked():
            raise JobAlreadyRunning(
                "Ya hay una ingesta en marcha -- espera a que termine o detenla."
            )

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_name = script_name.replace(".py", "")
        log_path = LOG_DIR / f"{ts}_{safe_name}.log"
        cmd = [sys.executable, "-u", str(SCRIPTS_DIR / script_name), *args]

        log_file = open(log_path, "w", encoding="utf-8", errors="replace")
        log_file.write(f"$ {' '.join(cmd)}\n\n")
        log_file.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

        _state.update(
            proc=proc,
            log_file=log_file,
            log_path=log_path,
            desc=desc,
            cmd=cmd,
            started_at=datetime.now(timezone.utc),
            finished_at=None,
            returncode=None,
        )
        return log_path


def status(log_tail_chars: int = 20000) -> dict:
    """Estado actual para el panel -- JSON-serializable directamente."""
    with _lock:
        proc = _state["proc"]
        if proc is None:
            return {"running": False, "started": False}

        running = _is_running_locked()
        log_path = _state["log_path"]
        tail = ""
        if log_path is not None and log_path.exists():
            try:
                data = log_path.read_text(encoding="utf-8", errors="replace")
                tail = data[-log_tail_chars:]
            except OSError:
                tail = "(no se pudo leer el log en este instante)"

        started_at = _state["started_at"]
        elapsed = None
        if started_at is not None:
            end = _state["finished_at"] or datetime.now(timezone.utc)
            elapsed = (end - started_at).total_seconds()

        return {
            "running": running,
            "started": True,
            "desc": _state["desc"],
            "cmd": " ".join(_state["cmd"]) if _state["cmd"] else None,
            "started_at": started_at.isoformat() if started_at else None,
            "finished_at": _state["finished_at"].isoformat() if _state["finished_at"] else None,
            "elapsed_seconds": elapsed,
            "returncode": _state["returncode"],
            "log_tail": tail,
        }


def stop() -> bool:
    """Termina el job en marcha (si lo hay). Seguro por diseño: las
    ingestas son reanudables (cachean cada respuesta en data/raw/ antes
    de escribir en la base de datos, ver raw_cache.py/ingest.py) -- cortar
    a mitad no deja nada corrupto, solo lo deja a medias para retomar
    despues. Devuelve True si habia algo que parar."""
    with _lock:
        proc = _state["proc"]
        if proc is None or proc.poll() is not None:
            return False
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    return True


def recent_log_files(limit: int = 20) -> list[Path]:
    """Ficheros de log mas recientes en disco -- incluye ingestas
    lanzadas en ejecuciones anteriores de la app, no solo la actual."""
    if not LOG_DIR.exists():
        return []
    return sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
