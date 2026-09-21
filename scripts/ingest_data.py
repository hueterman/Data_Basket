r"""Ingesta REAL (no de prueba) de varias fuentes a la base de datos
normalizada del proyecto: data/processed/basketball.db.

A diferencia de scripts/test_storage.py (que escribe en una base de
datos temporal y limita a unos pocos partidos), este script escribe en
la base de datos de verdad del proyecto y por defecto NO limita el
numero de partidos -- ingiere la temporada completa de cada fuente.

IMPORTANTE: ejecutar esto en tu propio equipo, en una terminal normal,
NO a traves del bridge de este chat (sin salida de red).

Por defecto ingiere las cuatro fuentes con temporada ya cerrada y
completa (rapidas de verdad, sin necesidad de acotar nada):
    NBA (season 2025-26), WNBA (2024), Euroliga (E2024), EuroCup (U2024)

CBB y ACB/FEB (Primera FEB Y Liga Femenina Endesa) se dejan fuera por
defecto:
- CBB: una temporada completa son miles de partidos a 3.6s/peticion
  (ver aviso de coste en el docstring de src/pipeline/ingest.py) --
  varias HORAS reales. Si se quiere incluir, usar --sources con --limit
  para acotarlo, nunca sin limite salvo que se acepte ese tiempo.
- ACB/FEB: a fecha de este script la temporada 2026-27 aun no tiene
  ningun partido jugado (confirmado en decisiones_proyecto.md) -- se
  ingerian equipos/calendario pero cero boxscores/tiros. Añadirlos
  cuando la liga arranque. Liga Femenina Endesa (`--sources lfendesa`,
  añadida 2026-09-20, ver src/scraper/feb.py) esta en la misma
  situacion -- confirmado en vivo que su temporada 2026-27 tampoco
  tiene partidos jugados todavia. Lo mismo aplica a LF Challenge, Liga
  Femenina 2 y Liga U (`--sources lfchallenge,lf2,ligau`, añadidas
  2026-09-20(b), ver "AMPLIACION 2026-09-20 (b)" en src/scraper/feb.py):
  las tres arrancan su 2026-27 en septiembre/octubre 2026, cero
  partidos jugados a fecha de esta sesion. Copa España (`--sources
  copaespaa`) es la excepcion parcial -- su edicion 2026 SI tiene ya un
  par de partidos jugados (liguilla de 4 equipos, formato pequeño) --
  pero se deja fuera de DEFAULT_SOURCES igualmente, por consistencia
  con el resto de fuentes FEB opt-in y porque su volumen es minimo.
  Segunda FEB y Tercera FEB (`--sources segundafeb,tercerafeb`,
  añadidas 2026-09-20(c)) tampoco tienen partidos jugados en su
  2026-27 a fecha de esta sesion -- misma situacion, fuera de
  DEFAULT_SOURCES.

Duracion real esperada (con los cuatro por defecto): WNBA es la mas
lenta por el rate limit de Sports Reference (3.6s/peticion, ~480
peticiones para boxscores+tiros de sus 240 partidos -> unos 30 min);
NBA/Euroliga/EuroCup no tienen ese limite forzado y deberian ir mas
rapido. Cuenta con un buen rato en total, no segundos.

REANUDABLE: cada respuesta se cachea en data/raw/ antes de escribirse
en la base de datos (ver raw_cache.py) -- si el script se corta a
mitad (Ctrl+C, se cierra la terminal...), volver a ejecutarlo retoma
donde se quedo sin repetir peticiones ya hechas.

Un fallo real en UNA fuente (incluido un 429/RateLimitedError) NO para
las demas -- se avisa y se sigue con la siguiente. Ver el resumen final
para saber que fuentes terminaron bien y cuales no.

AÑADIDO 2026-09-20(f): `--no-cache` fuerza rescrapear de verdad en vez
de reutilizar lo que ya haya en data/raw/ -- necesario, por ejemplo,
tras arreglar un bug de parseo real como el de FebSource.get_shots()
("m"/"t" mal interpretados, ver docstring del modulo, "BUGS REALES
corregidos 2026-09-20(e)"): la cache guarda el dato YA PARSEADO (con el
bug ya aplicado si lo habia), asi que re-ejecutar sin --no-cache
reutilizaria en silencio los mismos datos incorrectos en vez de volver
a pedirlos.

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\ingest_data.py
    python scripts\ingest_data.py --sources nba,wnba
    python scripts\ingest_data.py --sources cbb --limit 200   (CBB acotado)
    python scripts\ingest_data.py --no-players                (mas rapido)
    python scripts\ingest_data.py --sources feb --no-cache    (forzar rescrapear)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import get_connection, ingest_source
from src.pipeline.ingest import RateLimitedError

SEASONS = {
    "nba": "2025-26",
    "wnba": "2024",
    "euroleague": "E2024",
    "eurocup": "U2024",
    "cbb": "2024",
    # AcbSource.list_games() ya NO ignora season (ver src/scraper/acb.py,
    # reescrito para usar /es/liga/calendario?temporada=editionId) -- si
    # no se declara aqui, --sources acb pasaria el placeholder "?" y
    # list_games() lanzaria AcbSourceError de entrada (formato invalido).
    "acb": "2026-27",
    # Mismo motivo que acb -- FebSource.list_games()/list_teams() ya NO
    # ignoran season (ver src/scraper/feb.py), asi que sin esta entrada
    # --sources feb/lfendesa pasarian el placeholder "?" como año real.
    "feb": "2026",
    "lfendesa": "2026",
    # Añadidas 2026-09-20(b) -- mismo motivo/convencion que feb/lfendesa
    # (ver src/scraper/feb.py, "AMPLIACION 2026-09-20 (b)").
    "lfchallenge": "2026",
    "lf2": "2026",
    "ligau": "2026",
    "copaespaa": "2026",
    # Añadidas 2026-09-20(c) -- ver src/scraper/feb.py, "AMPLIACION
    # 2026-09-20 (c)".
    "segundafeb": "2026",
    "tercerafeb": "2026",
}

DEFAULT_SOURCES = ["nba", "wnba", "euroleague", "eurocup"]


def build_source(name: str):
    """Import perezoso por fuente -- así --sources nba,wnba no necesita
    tener instalado/funcionando todo lo demás (p. ej. curl_cffi para CBB)."""
    if name == "nba":
        from src.scraper.nba import NbaSource
        return NbaSource(season=SEASONS["nba"])
    if name == "wnba":
        from src.scraper.wnba import WnbaSource
        return WnbaSource(season=SEASONS["wnba"])
    if name in ("euroleague", "eurocup"):
        from src.scraper.euroleague import EuroleagueSource
        return EuroleagueSource(competition=name, season=SEASONS[name])
    if name == "nextgen":
        raise ValueError(
            "nextgen no tiene 'temporada actual' en este script -- a "
            "diferencia de euroliga/eurocup, NextGen (ANGT) no es una "
            "liga con una temporada continua, sino varios torneos de "
            "sede sueltos por año (ver docstring de "
            "src/scraper/euroleague.py). Usa "
            "scripts/ingest_historical.py --sources nextgen --seasons "
            "<codigos de sede reales, ej. JTB23> en su lugar."
        )
    if name == "cbb":
        from src.scraper.cbb import CbbSource
        return CbbSource(gender="men", season=SEASONS["cbb"])
    if name == "acb":
        from src.scraper.acb import AcbSource
        return AcbSource()
    if name in ("feb", "lfendesa", "lfchallenge", "lf2", "ligau", "copaespaa", "segundafeb", "tercerafeb"):
        from src.scraper.feb import FebSource
        return FebSource(competition=name, year=SEASONS[name])
    raise ValueError(f"fuente no soportada en este script: {name!r}")


def parse_args() -> dict:
    argv = sys.argv[1:]
    args = {"sources": DEFAULT_SOURCES, "fetch_players": True, "limit": None, "use_cache": True}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--sources":
            args["sources"] = argv[i + 1].split(","); i += 2
        elif arg == "--no-players":
            args["fetch_players"] = False; i += 1
        elif arg == "--limit":
            args["limit"] = int(argv[i + 1]); i += 2
        elif arg == "--no-cache":
            args["use_cache"] = False; i += 1
        else:
            raise SystemExit(f"Argumento no reconocido: {arg!r}")
    return args


def main() -> None:
    args = parse_args()
    conn = get_connection()  # base de datos real del proyecto (data/processed/basketball.db)
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    print(f"Base de datos: {db_path}")
    print(f"Fuentes: {', '.join(args['sources'])}")
    if args["limit"] is not None:
        print(f"(limitado a {args['limit']} partidos por fuente)")
    print()

    results: dict[str, object] = {}
    failed: dict[str, str] = {}

    for name in args["sources"]:
        season = SEASONS.get(name, "?")
        print(f"\n{'=' * 70}\n{name.upper()} (season={season})\n{'=' * 70}")
        try:
            source_obj = build_source(name)
        except Exception as exc:
            print(f"  no se pudo preparar la fuente -> {exc}")
            failed[name] = str(exc)
            continue

        started = time.monotonic()
        try:
            summary = ingest_source(
                source_obj,
                season=season,
                conn=conn,
                fetch_players=args["fetch_players"],
                limit=args["limit"],
                use_cache=args["use_cache"],
            )
            results[name] = summary
            print(f"\n{summary}")
        except RateLimitedError as exc:
            elapsed = time.monotonic() - started
            print(f"\n  RATE LIMIT real en {name} (tras {elapsed:.0f}s) -> {exc}")
            print(
                f"  Ingesta de {name} abortada -- se sigue con la siguiente fuente, "
                f"pero NO reintentes {name} hoy (posible bloqueo de IP/sesión hasta "
                f"24h, ver sports_reference.py)."
            )
            failed[name] = "RateLimitedError"
        except Exception as exc:
            elapsed = time.monotonic() - started
            print(f"\n  fallo inesperado en {name} (tras {elapsed:.0f}s) -> {exc}")
            print(f"  se sigue con la siguiente fuente.")
            failed[name] = str(exc)

    conn.close()

    print(f"\n{'=' * 70}\nRESUMEN\n{'=' * 70}")
    for name, summary in results.items():
        print(f"  OK    {summary}")
    for name, reason in failed.items():
        print(f"  FALLO {name}: {reason}")
    if not results and not failed:
        print("  (nada que ingerir -- revisa --sources)")


if __name__ == "__main__":
    main()
