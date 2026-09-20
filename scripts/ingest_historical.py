r"""Ingesta de TEMPORADAS HISTORICAS adicionales (Fase 7 del plano) --
complementa a scripts/ingest_data.py (que ingiere solo la temporada mas
reciente y cerrada de cada fuente, una por fuente). Este script repite
la ingesta para una LISTA de temporadas pasadas por fuente, pensado para
construir una base de datos con varios años de historico de jugadores y
partidos con la que sacar conclusiones o entrenar IA mas adelante segun
haga falta -- no un modelo concreto todavia (instruccion del usuario,
2026-09-09).

Solo cubre fuentes cuyo scraper SI soporta de verdad pedir una temporada
distinta a la actual -- verificado leyendo el propio codigo de cada
modulo (ver decisiones_proyecto.md, "NUEVO OBJETIVO DE ALCANCE..." y
"ACB HISTORICO RESUELTO..."): NBA, Euroliga, EuroCup, ACB, FEB (Primera
FEB) y LFENDESA (Liga Femenina Endesa) por defecto aqui.

FEB (feb.py) AÑADIDA el 2026-09-20 tras investigar en vivo por que
list_teams()/list_games() ignoraban `season` (ver docstring del propio
modulo, "HISTORICO Y BOXSCORE RESUELTOS EN VIVO"): la conclusion previa
de que `t=` se ignoraba solo era cierta para `resultados.aspx` en su
forma de query-string -- `calendario.aspx?g=&t=&nm=` SI respeta la
temporada y trae la temporada completa (34 jornadas de Liga Regular) en
una sola peticion. `season` para FEB es el año de INICIO de temporada
como string (p. ej. "2024" para 2024/25), igual que el `t=` real del
sitio -- confirmado con 2010, 2022 y 2024 ademas de la 2026 en curso.
Igual que ACB (ver parrafo de arriba), la temporada 2026-27 en curso
todavia no tiene ningun partido jugado (por eso scripts/ingest_data.py
deja FEB fuera de sus DEFAULT_SOURCES, aunque SI sabe construirla) --
no hay ninguna temporada "actual" con datos reales que excluir aqui,
asi que HISTORICAL_SEASONS["feb"] trae 5 temporadas completas, no 4.

LFENDESA (Liga Femenina Endesa) AÑADIDA el mismo dia, a peticion
explicita del usuario ("el FEB solo ingerimos datos de competiciones
masculinas o tambien femeninas"), tras confirmar en vivo que
`calendario.aspx?g=4&t=&nm=lfendesa` funciona EXACTAMENTE igual que
Primera FEB -- mismo `calendario.aspx`/`Partido.aspx`/`Equipo.aspx`,
mismos selectores, ni un solo cambio de parseo (ver docstring de
`FebSource`, seccion "MULTIPLES COMPETICIONES", para el detalle
completo y el listado de otras competiciones del mismo sitio que
TODAVIA no estan soportadas, incluidas Segunda FEB y Tercera FEB --
las antiguas LEB Oro/LEB Plata). `FebSource` ahora se construye con
`FebSource(competition=..., year=...)` en vez de
`FebSource(group_code=..., year=..., slug=...)` -- cada competicion
(`"feb"`/`"lfendesa"`) cae en su propio `source` en la base de datos,
nunca mezcladas (antes `source` era un atributo de clase fijo a
`"feb"`, lo que habria mezclado ambas bajo el mismo `source` si se
hubiera añadido la femenina sin este cambio). Misma situacion de
temporada actual que Primera FEB: 2026-27 sin jugar todavia, por eso
`HISTORICAL_SEASONS["lfendesa"]` tambien trae 5 temporadas completas.

LF CHALLENGE, LIGA FEMENINA 2, LIGA U y COPA ESPAÑA AÑADIDAS el
2026-09-20(b) (peticion explicita del usuario: "implementa el resto de
categorias: LF Challenge, Liga Femenina 2, Liga U, Copa España...") --
ver "AMPLIACION 2026-09-20 (b)" en src/scraper/feb.py para el detalle
completo de la verificacion en vivo. Resumen relevante para este
script:

- lfchallenge y lf2 SI tienen historico fiable confirmado en vivo (mas
  de 5 temporadas reales y distintas cada una) -- 5 temporadas
  completas, mismo patron que feb/lfendesa.
- copaespaa tiene un RIESGO REAL confirmado en vivo ("temporada-
  fantasma"): pedir `t=2023` o anterior para g=73 devuelve EN SILENCIO
  la edicion de `t=2026` en vez de datos propios (o un error) -- solo
  t=2024, t=2025 y t=2026 son ediciones reales y distintas. Por eso
  `HISTORICAL_SEASONS["copaespaa"]` trae SOLO 3 temporadas, nunca 5 --
  ampliarlo a años anteriores ingeriria partidos duplicados bajo una
  temporada equivocada sin avisar (el guardia nuevo en
  `FebSource._list_games_raw()` lo bloquearia con un error, no lo
  dejaria pasar en silencio, pero de todas formas no tiene sentido
  pedir esos años).
- segundafeb y tercerafeb (Segunda FEB/Tercera FEB, antiguas LEB Oro/
  LEB Plata) AÑADIDAS el 2026-09-20(c), a peticion explicita del
  usuario ("Incluye Segunda FEB, Tercera FEB") -- ver "AMPLIACION
  2026-09-20 (c)" en src/scraper/feb.py. segundafeb es tan robusta
  como feb/lfendesa (7 temporadas seguidas probadas en vivo, todas
  ligas completas de 26 jornadas/182 partidos) -- 5 temporadas
  completas igual que las demas. tercerafeb tiene un CAMBIO
  ESTRUCTURAL REAL (no un bug, confirmado con datos reales y distintos
  cada temporada): solo desde 2026-27 es una liga nacional completa --
  las 5 temporadas de HISTORICAL_SEASONS (2021-2025) son solo una fase
  final/promocion pequeña de 2 a 4 equipos y 1 a 3 partidos, NO la
  temporada regular completa (que hasta ahora se gestionaba fuera de
  este sitio). Se incluyen igualmente porque son datos reales, pero no
  son comparables en volumen a la temporada 2026-27 en adelante -- no
  confundir con un fallo de ingesta si un año de tercerafeb trae solo
  1-2 partidos.
- ligau tiene el MISMO riesgo de temporada-fantasma para t<2025 (misma
  edicion de t=2026 repetida) Y ademas su unica edicion "distinta"
  (t=2025) resulto ser un unico partido suelto, no una temporada real
  -- no hay ningun año con historico fiable todavia. Por eso ligau NO
  tiene entrada en HISTORICAL_SEASONS ni esta en DEFAULT_SOURCES de
  este script -- sigue soportado via `build_source()` para quien quiera
  forzar `--sources ligau --seasons 2026` a mano (la temporada actual,
  sin jugar todavia a fecha de esta sesion), pero sin ningun valor por
  defecto que pueda esconder el problema.

ACB (acb.py) SI tiene temporada historica real desde la sesion del
2026-09-09: `list_games(season)` pide
`/es/liga/calendario?temporada={editionId}`, que cubre las 43
temporadas de la ACB (1983-84 a 2026-27) con 1 sola peticion GET por
temporada -- ver docstring de AcbSource.list_games() para el detalle
completo. A diferencia de NBA/Euroliga/EuroCup, ACB NO tiene ninguna
temporada "actual" ya cargada por scripts/ingest_data.py (la 2026-27
todavia no ha empezado, 0 partidos jugados a fecha de esta sesion) --
por eso HISTORICAL_SEASONS["acb"] trae las 5 temporadas completas
(2025-26 a 2021-22), no solo 4 como las demas fuentes.

WNBA y CBB tambien soportan temporada real (igual que NBA) y podrian
añadirse aqui con el mismo patron el dia que se quiera -- se dejan fuera
de HISTORICAL_SEASONS/DEFAULT_SOURCES por ahora porque no se ha pedido
todavia (y CBB en concreto es caro: ~11 min fijos de listado POR
TEMPORADA ademas del coste por partido, ver ingest.py). Recuerda que
ambas son NON_TRAINABLE_SOURCES pase lo que pase (train.py las excluye
siempre, sin importar cuantas temporadas se carguen).

NEXTGEN (ANGT), resuelto en vivo el 2026-09-19 (ver docstring de
src/scraper/euroleague.py, aviso "NEXTGEN (ANGT) RESUELTO EN VIVO"):
soportado aqui via el mismo build_source() que euroleague/eurocup,
pero SIN entrada en HISTORICAL_SEASONS -- a diferencia de esas dos
competiciones, NextGen no tiene un "season" por año (cada edicion
anual son ~5 seasonCode independientes, uno por sede clasificatoria
mas la Final Eight), asi que no hay una lista de "las 4 anteriores"
que se pueda dar por defecto sin adivinar que sedes contarian. Usar
SIEMPRE --seasons con codigos de sede reales (formato "JTxx##", p. ej.
"JTB23" = Belgrado 2023-24) y una unica fuente en --sources (la regla
que ya exige parse_args() mas abajo). Para saber que codigos existen
para un año concreto no hay atajo aqui todavia: GET
https://api-live.euroleague.net/v2/competitions/J/seasons (JSON,
Accept: application/json) y filtrar las entradas por su campo "year".
Ejemplo:
    python scripts\ingest_historical.py --sources nextgen --seasons JTB23,JTP23,JTD23,JTC23,JTF423

Temporadas por defecto (verificadas en vivo el 2026-09-09, ver
decisiones_proyecto.md): las 5 mas recientes YA CERRADAS de cada fuente,
excluyendo la que scripts/ingest_data.py ya carga por defecto (NBA
2025-26, Euroliga E2024, EuroCup U2024) -- este script solo añade las 4
que faltan por fuente para llegar a 5 en total (5 completas para ACB,
ver parrafo de arriba). NO incluye la temporada en curso (NBA no tiene
una en curso ahora mismo; Euroliga/EuroCup E2026/U2026 y ACB 2026-27
estan activas por fecha pero probablemente sin partidos jugados
todavia -- no se han comprobado en vivo, no se incluyen aqui a
proposito).

REANUDABLE (igual que ingest_data.py): cada respuesta se cachea en
data/raw/ -- si se corta a mitad, volver a ejecutar retoma sin repetir
peticiones ya hechas. Un fallo real en una (fuente, temporada) --
incluido un RateLimitedError -- NO para las demas combinaciones.

Uso:
    cd D:\Proyectos\Data_Basket
    python scripts\ingest_historical.py
    python scripts\ingest_historical.py --sources nba
    python scripts\ingest_historical.py --sources nba --seasons 2020-21,2019-20
    python scripts\ingest_historical.py --no-players
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import get_connection, ingest_source
from src.pipeline.ingest import RateLimitedError

# Las temporadas que faltan por fuente para llegar a 5 en total. Para
# NBA/Euroliga/EuroCup son 4 (la 5a ya la carga scripts/ingest_data.py
# como "actual": NBA 2025-26, Euroliga E2024, EuroCup U2024). Para ACB
# son las 5 completas (2025-26 a 2021-22): no hay ninguna temporada
# "actual" ya cargada, la 2026-27 no ha empezado (ver docstring del
# modulo). Formato de season string: el mismo que ya usa ingest_data.py
# para cada fuente (confirmado que funciona en el propio codigo del
# scraper, no un formato nuevo inventado aqui) -- para ACB es "YYYY-YY",
# el mismo que traduce AcbSource._edition_id_from_season().
HISTORICAL_SEASONS: dict[str, list[str]] = {
    "nba": ["2024-25", "2023-24", "2022-23", "2021-22"],
    "euroleague": ["E2025", "E2023", "E2022", "E2021"],
    "eurocup": ["U2025", "U2023", "U2022", "U2021"],
    "acb": ["2025-26", "2024-25", "2023-24", "2022-23", "2021-22"],
    # Como ACB: sin ninguna temporada "actual" con datos reales que
    # excluir (2026-27 sin jugar todavia), 5 temporadas completas. El
    # año es el de INICIO de temporada, formato real de `t=` en
    # calendario.aspx (ver docstring del modulo y de FebSource).
    "feb": ["2025", "2024", "2023", "2022", "2021"],
    # Liga Femenina Endesa -- misma situacion que "feb" (ver docstring
    # del modulo, "LFENDESA (Liga Femenina Endesa) AÑADIDA").
    "lfendesa": ["2025", "2024", "2023", "2022", "2021"],
    # LF Challenge y Liga Femenina 2 -- añadidas 2026-09-20(b), mismo
    # patron de 5 temporadas que feb/lfendesa, historico confirmado
    # fiable en vivo (ver docstring del modulo).
    "lfchallenge": ["2025", "2024", "2023", "2022", "2021"],
    "lf2": ["2025", "2024", "2023", "2022", "2021"],
    # Copa España -- SOLO 3 temporadas, nunca 5: t=2023 y anteriores
    # sufren el riesgo de "temporada-fantasma" confirmado en vivo (ver
    # docstring del modulo y "AMPLIACION 2026-09-20 (b)" en
    # src/scraper/feb.py) -- devuelven en silencio la edicion de t=2026
    # en vez de datos propios. NO ampliar sin volver a verificar en vivo.
    "copaespaa": ["2026", "2025", "2024"],
    # ligau (Liga U) NO tiene entrada aqui a proposito -- sin historico
    # fiable todavia (ver docstring del modulo), solo soportada via
    # --sources ligau --seasons 2026 explicito.
    # Segunda FEB -- tan robusta como feb/lfendesa (ver docstring del
    # modulo).
    "segundafeb": ["2025", "2024", "2023", "2022", "2021"],
    # Tercera FEB -- datos reales pero de una fase final/promocion
    # pequeña antes de 2026-27, NO la temporada regular completa (ver
    # docstring del modulo, "segundafeb y tercerafeb AÑADIDAS").
    "tercerafeb": ["2025", "2024", "2023", "2022", "2021"],
}

DEFAULT_SOURCES = [
    "nba", "euroleague", "eurocup", "acb", "feb", "lfendesa",
    # Añadidas 2026-09-20(b) -- ver HISTORICAL_SEASONS de arriba. ligau
    # se deja fuera a proposito (sin historico fiable todavia).
    "lfchallenge", "lf2", "copaespaa",
    # Añadidas 2026-09-20(c).
    "segundafeb", "tercerafeb",
]


def build_source(name: str, season: str):
    """Import perezoso por fuente, igual que ingest_data.py -- una
    (source, season) concreta cada vez, en vez de una season fija global."""
    if name == "nba":
        from src.scraper.nba import NbaSource
        return NbaSource(season=season)
    if name in ("euroleague", "eurocup", "nextgen"):
        from src.scraper.euroleague import EuroleagueSource
        return EuroleagueSource(competition=name, season=season)
    if name == "wnba":
        from src.scraper.wnba import WnbaSource
        return WnbaSource(season=season)
    if name == "cbb":
        from src.scraper.cbb import CbbSource
        return CbbSource(gender="men", season=season)
    if name == "acb":
        from src.scraper.acb import AcbSource
        return AcbSource()
    if name in ("feb", "lfendesa", "lfchallenge", "lf2", "ligau", "copaespaa", "segundafeb", "tercerafeb"):
        from src.scraper.feb import FebSource
        return FebSource(competition=name, year=season)
    raise ValueError(f"fuente no soportada en este script: {name!r}")


def parse_args() -> dict:
    argv = sys.argv[1:]
    args = {"sources": DEFAULT_SOURCES, "seasons": None, "fetch_players": True, "limit": None}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--sources":
            args["sources"] = argv[i + 1].split(","); i += 2
        elif arg == "--seasons":
            args["seasons"] = argv[i + 1].split(","); i += 2
        elif arg == "--no-players":
            args["fetch_players"] = False; i += 1
        elif arg == "--limit":
            args["limit"] = int(argv[i + 1]); i += 2
        else:
            raise SystemExit(f"Argumento no reconocido: {arg!r}")
    if args["seasons"] is not None and len(args["sources"]) != 1:
        raise SystemExit(
            "--seasons solo se puede usar con exactamente una fuente en --sources "
            "(cada fuente tiene su propio formato de codigo de temporada -- "
            "mezclarlas en una sola lista seria adivinar cual va con cual)."
        )
    return args


def main() -> None:
    args = parse_args()
    conn = get_connection()
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    print(f"Base de datos: {db_path}")

    plan: list[tuple[str, str]] = []
    for name in args["sources"]:
        if name not in HISTORICAL_SEASONS and args["seasons"] is None:
            raise SystemExit(
                f"{name!r} no tiene temporadas historicas por defecto todavia -- "
                f"pasa --seasons explicitamente (una sola fuente a la vez)."
            )
        seasons = args["seasons"] if args["seasons"] is not None else HISTORICAL_SEASONS[name]
        plan.extend((name, season) for season in seasons)

    print(f"Plan: {len(plan)} (fuente, temporada) -- {plan}")
    if args["limit"] is not None:
        print(f"(limitado a {args['limit']} partidos por temporada)")
    print()

    results: dict[tuple[str, str], object] = {}
    failed: dict[tuple[str, str], str] = {}

    for name, season in plan:
        print(f"\n{'=' * 70}\n{name.upper()} (season={season})\n{'=' * 70}")
        try:
            source_obj = build_source(name, season)
        except Exception as exc:
            print(f"  no se pudo preparar la fuente -> {exc}")
            failed[(name, season)] = str(exc)
            continue

        started = time.monotonic()
        try:
            summary = ingest_source(
                source_obj, season=season, conn=conn,
                fetch_players=args["fetch_players"], limit=args["limit"],
            )
            results[(name, season)] = summary
            print(f"\n{summary}")
        except RateLimitedError as exc:
            elapsed = time.monotonic() - started
            print(f"\n  RATE LIMIT real en {name}/{season} (tras {elapsed:.0f}s) -> {exc}")
            print(
                f"  Ingesta de {name}/{season} abortada -- se sigue con la siguiente, "
                f"pero no reintentes {name} hoy (posible bloqueo de IP/sesion hasta 24h)."
            )
            failed[(name, season)] = "RateLimitedError"
        except Exception as exc:
            elapsed = time.monotonic() - started
            print(f"\n  fallo inesperado en {name}/{season} (tras {elapsed:.0f}s) -> {exc}")
            print(f"  se sigue con la siguiente (fuente, temporada).")
            failed[(name, season)] = str(exc)

    conn.close()

    print(f"\n{'=' * 70}\nRESUMEN\n{'=' * 70}")
    for (name, season), summary in results.items():
        print(f"  OK    {name}/{season}: {summary}")
    for (name, season), reason in failed.items():
        print(f"  FALLO {name}/{season}: {reason}")
    if not results and not failed:
        print("  (nada que ingerir -- revisa --sources/--seasons)")


if __name__ == "__main__":
    main()
