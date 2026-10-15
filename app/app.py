"""Explorador de datos del proyecto (fase "interfaz grafica",
sesion 2026-09-18) -- app Flask de SOLO LECTURA sobre
data/processed/basketball.db.

No toca el pipeline de scraping/ingesta para nada: usa
src.pipeline.db.get_connection() (la misma funcion que usan los scripts
de ingesta) para abrir el MISMO fichero de base de datos, pero aqui solo
se ejecutan SELECT -- no hay ninguna ruta que escriba en la base de
datos. Seguir lanzando las ingestas por linea de comandos
(scripts/ingest_data.py, scripts/ingest_historical.py) como hasta ahora;
esta app es solo para mirar lo que ya hay guardado.

Uso:
    pip install -r requirements.txt   # ya incluye flask
    python app/app.py
    -> abre http://127.0.0.1:5050 en el navegador

    Puerto configurable sin tocar el codigo si 5050 tambien estuviera
    ocupado -- variable de entorno PORT, p. ej.:
        PORT=5051 python app/app.py            (Linux/Mac)
        set PORT=5051 && python app/app.py     (Windows cmd)
        $env:PORT=5051; python app/app.py      (PowerShell)

Estructura deliberada: toda la logica de negocio real (que fuentes
existen, como se calibran las coordenadas de tiro, etc.) sigue viviendo
en src/ -- esta app solo hace consultas SQL y las pasa a las plantillas.
Así el pipeline sigue funcionando exactamente igual por CLI si nunca se
usa la app, y un cambio de esquema en src/pipeline/schema.py se nota
aqui sin tener que duplicar nada.
"""
from __future__ import annotations

import math
import os
import sys
from io import BytesIO
from pathlib import Path

# Para poder ejecutar "python app/app.py" desde cualquier sitio y que
# "import src...." seguido funcione igual que en los scripts de la raiz.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import perf
perf.apply_thread_limits()  # antes de importar numpy/sklearn/xgboost (ver src/perf.py)

from flask import Flask, Response, abort, flash, redirect, render_template, request, url_for

import jobs
from src.pipeline.db import get_connection
from src.pipeline import clubs
from src.pipeline.repository import team_seasons
from src.scraper.base import ALL_SOURCES  # noqa: F401 (documenta el conjunto valido)

app = Flask(__name__)
# Solo para poder usar flash() en el panel de ingestas (mensajes de error
# de validacion del formulario) -- app puramente local de un usuario, sin
# nada sensible en la sesion, así que una clave nueva en cada arranque
# basta (no hace falta que sobreviva a un reinicio de la app).
app.secret_key = os.urandom(24)

PAGE_SIZE = 50

# Atribucion obligatoria por fuente (ver decisiones_proyecto.md, entrada
# de terminos de uso) -- se muestra en las paginas que enseñan datos de
# esa fuente, igual que ya se hace en el notebook.
ATTRIBUTION = {
    "euroleague": "Fuente de datos: www.euroleague.net",
    "eurocup": "Fuente de datos: www.euroleague.net",
    "nextgen": "Fuente de datos: www.euroleague.net",
    "cbb": "Fuente de datos: Sports Reference (sports-reference.com/cbb)",
    "wnba": "Fuente de datos: Sports Reference (basketball-reference.com/wnba)",
}

# Fuentes con coordenadas de tiro calibradas (ver
# SHOT_COORD_CALIBRATION en src/features.py) -- acb/feb no tienen
# get_shots() confirmado o implementado todavia, y cbb no tiene
# gráfico de tiro en la fuente original (ver base.py/cbb.py). nextgen
# añadido el 2026-09-19 (mismo proveedor/calibracion que euroleague y
# eurocup, confirmado en vivo).
SHOT_CHART_SOURCES = {"nba", "euroleague", "eurocup", "wnba", "nextgen"}


def db():
    """Conexion nueva por peticion -- SQLite + Flask de un solo proceso
    no necesita pool de conexiones para esto."""
    return get_connection()


def paginate(total: int, page: int, page_size: int = PAGE_SIZE):
    pages = max(1, math.ceil(total / page_size))
    page = max(1, min(page, pages))
    offset = (page - 1) * page_size
    return page, pages, offset


def _sources_list(conn):
    return [r[0] for r in conn.execute("SELECT source FROM sources ORDER BY source")]


# --------------------------------------------------------------------- #
# Inicio
# --------------------------------------------------------------------- #


@app.route("/")
def index():
    conn = db()
    counts = {}
    for table in ("teams", "players", "games", "team_stats", "player_stats", "shots"):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    by_source = conn.execute(
        """
        SELECT
            s.source,
            s.trainable,
            (SELECT COUNT(*) FROM teams  t WHERE t.source = s.source) AS n_teams,
            (SELECT COUNT(*) FROM players p WHERE p.source = s.source) AS n_players,
            (SELECT COUNT(*) FROM games  g WHERE g.source = s.source) AS n_games,
            (SELECT COUNT(*) FROM games  g WHERE g.source = s.source AND g.played = 1) AS n_played,
            (SELECT COUNT(*) FROM shots sh WHERE sh.source = s.source) AS n_shots
        FROM sources s
        ORDER BY s.source
        """
    ).fetchall()
    return render_template(
        "index.html", counts=counts, by_source=by_source, attribution=ATTRIBUTION,
        shot_chart_sources=SHOT_CHART_SOURCES,
    )


# --------------------------------------------------------------------- #
# Equipos
# --------------------------------------------------------------------- #


@app.route("/equipos")
def teams():
    conn = db()
    source = request.args.get("source") or ""
    q = request.args.get("q") or ""

    sql = "SELECT source, team_id, name FROM teams WHERE 1=1"
    params: list = []
    if source:
        sql += " AND source = ?"
        params.append(source)
    if q:
        sql += " AND name LIKE ?"
        params.append(f"%{q}%")
    sql += " ORDER BY source, name"
    rows = conn.execute(sql, params).fetchall()

    # En FEB/Liga Femenina el mismo club tiene un team_id distinto cada
    # temporada: se muestra la temporada para distinguir las filas "repetidas".
    seasons = team_seasons(conn)
    season_text = {
        (r["source"], str(r["team_id"])): ", ".join(seasons.get((r["source"], str(r["team_id"])), []))
        for r in rows
    }
    rows = sorted(rows, key=lambda r: (
        r["source"], r["name"], (seasons.get((r["source"], str(r["team_id"]))) or [""])[0]))

    return render_template(
        "teams.html", teams=rows, sources=_sources_list(conn), source=source, q=q,
        season_text=season_text,
    )


@app.route("/equipos/<source>/<team_id>")
def team_detail(source, team_id):
    conn = db()
    team = conn.execute(
        "SELECT source, team_id, name FROM teams WHERE source=? AND team_id=?",
        (source, team_id),
    ).fetchone()
    if team is None:
        abort(404)

    roster = conn.execute(
        "SELECT player_id, name FROM players WHERE source=? AND team_id=? ORDER BY name",
        (source, team_id),
    ).fetchall()

    games = conn.execute(
        """
        SELECT game_id, season, date, home_team_id, away_team_id,
               home_score, away_score, played
        FROM games
        WHERE source = ? AND (home_team_id = ? OR away_team_id = ?)
        ORDER BY date DESC
        """,
        (source, team_id, team_id),
    ).fetchall()

    wins = losses = 0
    for g in games:
        if not g["played"] or g["home_score"] is None or g["away_score"] is None:
            continue
        is_home = g["home_team_id"] == team_id
        own = g["home_score"] if is_home else g["away_score"]
        opp = g["away_score"] if is_home else g["home_score"]
        if own > opp:
            wins += 1
        elif own < opp:
            losses += 1

    # Nombres de los rivales, para no enseñar solo IDs en el calendario.
    opponent_names = {}
    for g in games:
        opp_id = g["away_team_id"] if g["home_team_id"] == team_id else g["home_team_id"]
        if opp_id in opponent_names:
            continue
        row = conn.execute(
            "SELECT name FROM teams WHERE source=? AND team_id=?", (source, opp_id)
        ).fetchone()
        opponent_names[opp_id] = row["name"] if row else opp_id

    return render_template(
        "team_detail.html",
        team=team, roster=roster, games=games, wins=wins, losses=losses,
        opponent_names=opponent_names, attribution=ATTRIBUTION.get(source),
    )


# --------------------------------------------------------------------- #
# Jugadores
# --------------------------------------------------------------------- #


@app.route("/jugadores")
def players():
    conn = db()
    source = request.args.get("source") or ""
    q = request.args.get("q") or ""
    page = max(1, int(request.args.get("page", 1) or 1))

    where = "WHERE 1=1"
    params: list = []
    if source:
        where += " AND source = ?"
        params.append(source)
    if q:
        where += " AND name LIKE ?"
        params.append(f"%{q}%")

    total = conn.execute(f"SELECT COUNT(*) FROM players {where}", params).fetchone()[0]
    page, pages, offset = paginate(total, page)
    rows = conn.execute(
        f"SELECT source, player_id, name, team_id FROM players {where} "
        f"ORDER BY name LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, offset],
    ).fetchall()

    return render_template(
        "players.html", players=rows, sources=_sources_list(conn), source=source, q=q,
        page=page, pages=pages, total=total,
    )


@app.route("/jugadores/<source>/<player_id>")
def player_detail(source, player_id):
    conn = db()
    player = conn.execute(
        "SELECT source, player_id, name, team_id FROM players WHERE source=? AND player_id=?",
        (source, player_id),
    ).fetchone()
    if player is None:
        abort(404)

    stat_lines = conn.execute(
        """
        SELECT ps.game_id, g.season, g.date, ps.team_id, ps.minutes,
               ps.points, ps.rebounds, ps.assists, ps.val, ps.plus_minus
        FROM player_stats ps
        JOIN games g ON g.source = ps.source AND g.game_id = ps.game_id
        WHERE ps.source = ? AND ps.player_id = ?
        ORDER BY g.date DESC
        """,
        (source, player_id),
    ).fetchall()

    n = len(stat_lines)
    averages = {}
    for key in ("minutes", "points", "rebounds", "assists", "val"):
        vals = [r[key] for r in stat_lines if r[key] is not None]
        averages[key] = round(sum(vals) / len(vals), 1) if vals else None

    return render_template(
        "player_detail.html", player=player, stat_lines=stat_lines, n=n,
        averages=averages, attribution=ATTRIBUTION.get(source),
    )


# --------------------------------------------------------------------- #
# Partidos
# --------------------------------------------------------------------- #


@app.route("/partidos")
def games():
    conn = db()
    source = request.args.get("source") or ""
    season = request.args.get("season") or ""
    team = request.args.get("team") or ""
    page = max(1, int(request.args.get("page", 1) or 1))

    # Equipos del desplegable (solo con una fuente elegida: los ids son por fuente).
    # Si el equipo pedido no es de esta fuente (p. ej. se cambio de fuente), se ignora.
    team_choices = clubs.team_options(conn, source) if source else []
    if team and team not in {k for k, _ in team_choices}:
        team = ""

    where = "WHERE 1=1"
    params: list = []
    if source:
        where += " AND source = ?"
        params.append(source)
    if season:
        where += " AND season = ?"
        params.append(season)
    if team:
        ids = clubs.team_ids_for(conn, source, team)
        marks = ",".join("?" * len(ids))
        where += f" AND (home_team_id IN ({marks}) OR away_team_id IN ({marks}))"
        params += ids + ids

    total = conn.execute(f"SELECT COUNT(*) FROM games {where}", params).fetchone()[0]
    page, pages, offset = paginate(total, page)
    rows = conn.execute(
        f"""
        SELECT source, game_id, season, date, home_team_id, away_team_id,
               home_score, away_score, played
        FROM games {where}
        ORDER BY date DESC LIMIT ? OFFSET ?
        """,
        params + [PAGE_SIZE, offset],
    ).fetchall()

    # Nombres de equipo para las filas de esta pagina (evita N+1 raro:
    # como mucho PAGE_SIZE*2 nombres distintos, no una tabla entera).
    team_names = {}
    for r in rows:
        for tid in (r["home_team_id"], r["away_team_id"]):
            key = (r["source"], tid)
            if key in team_names:
                continue
            row = conn.execute(
                "SELECT name FROM teams WHERE source=? AND team_id=?", key
            ).fetchone()
            team_names[key] = row["name"] if row else tid

    seasons = []
    if source:
        seasons = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT season FROM games WHERE source=? ORDER BY season DESC",
                (source,),
            )
        ]

    return render_template(
        "games.html",
        games=rows, sources=_sources_list(conn), source=source, season=season,
        team=team, team_choices=team_choices, seasons=seasons, page=page, pages=pages, total=total, team_names=team_names,
    )


@app.route("/partidos/<source>/<game_id>")
def game_detail(source, game_id):
    conn = db()
    game = conn.execute(
        "SELECT * FROM games WHERE source=? AND game_id=?", (source, game_id)
    ).fetchone()
    if game is None:
        abort(404)

    team_stats = conn.execute(
        "SELECT * FROM team_stats WHERE source=? AND game_id=?", (source, game_id)
    ).fetchall()
    player_stats = conn.execute(
        """
        SELECT * FROM player_stats WHERE source=? AND game_id=?
        ORDER BY team_id, points DESC
        """,
        (source, game_id),
    ).fetchall()

    team_names = {}
    for tid in (game["home_team_id"], game["away_team_id"]):
        row = conn.execute(
            "SELECT name FROM teams WHERE source=? AND team_id=?", (source, tid)
        ).fetchone()
        team_names[tid] = row["name"] if row else tid

    n_shots = conn.execute(
        "SELECT COUNT(*) FROM shots WHERE source=? AND game_id=?", (source, game_id)
    ).fetchone()[0]

    return render_template(
        "game_detail.html",
        game=game, team_stats=team_stats, player_stats=player_stats,
        team_names=team_names, n_shots=n_shots, attribution=ATTRIBUTION.get(source),
        has_shot_chart=(source in SHOT_CHART_SOURCES and n_shots > 0),
    )


@app.route("/partidos/<source>/<game_id>/tiros.png")
def shot_chart(source, game_id):
    """Shot chart de un partido, generado al vuelo (no se guarda en disco).

    404 explicito (no adivina) si la fuente no tiene coordenadas de tiro
    calibradas o el partido no tiene tiros guardados -- mismo criterio
    "avisa y no adivina" que el resto del proyecto.
    """
    if source not in SHOT_CHART_SOURCES:
        abort(404, f"{source} no tiene coordenadas de tiro calibradas todavia")

    conn = db()
    shots = conn.execute(
        "SELECT loc_x, loc_y, made FROM shots WHERE source=? AND game_id=?",
        (source, game_id),
    ).fetchall()
    if not shots:
        abort(404, "Este partido no tiene tiros guardados")

    # Cache en disco (perfil "pi" o SHOT_CACHE=1, ver src/perf.py): generar el
    # PNG con matplotlib es lo mas caro de esta app en una Raspberry Pi. La
    # clave incluye el numero de tiros y de anotados, asi que si una
    # reingesta cambia los datos del partido se genera uno nuevo solo.
    from src import perf
    cache_file = None
    if perf.settings()["shot_cache"]:
        import hashlib
        key = hashlib.sha1(
            f"{source}|{game_id}|{len(shots)}|{sum(1 for s in shots if s['made'])}".encode()
        ).hexdigest()
        cache_file = perf.SHOT_CACHE_DIR / f"{key}.png"
        try:
            if cache_file.exists():
                return Response(cache_file.read_bytes(), mimetype="image/png")
        except OSError:
            cache_file = None

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from src.features import SHOT_COORD_CALIBRATION
    from src.viz import draw_court, draw_court_fiba

    if source == "nba":
        # Unidad nativa (decimas de pie) -- sin convertir, igual que en
        # el notebook (seccion 4).
        xs = [s["loc_x"] for s in shots]
        ys = [s["loc_y"] for s in shots]
        fig, ax = plt.subplots(figsize=(6, 5.7))
        draw_court(ax)
    else:
        to_m = SHOT_COORD_CALIBRATION[source]["to_meters"]
        xy = [to_m(s["loc_x"], s["loc_y"]) for s in shots]
        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        fig, ax = plt.subplots(figsize=(6, 6.2))
        draw_court_fiba(ax)

    made = [bool(s["made"]) for s in shots]
    xs_made = [x for x, m in zip(xs, made) if m]
    ys_made = [y for y, m in zip(ys, made) if m]
    xs_miss = [x for x, m in zip(xs, made) if not m]
    ys_miss = [y for y, m in zip(ys, made) if not m]

    ax.scatter(xs_made, ys_made, c="#2a6fdb", s=18, alpha=0.75, label=f"Anotado ({len(xs_made)})")
    ax.scatter(xs_miss, ys_miss, c="#d9822b", s=18, alpha=0.6, marker="x", label=f"Fallado ({len(xs_miss)})")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.04), ncol=2, fontsize=8, framealpha=0.9)

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    png = buf.getvalue()
    if cache_file is not None:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_bytes(png)
            tmp.replace(cache_file)  # escritura atomica: nunca se sirve un PNG a medias
        except OSError:
            pass  # la cache es opcional: si falla, se sirve igual
    return Response(png, mimetype="image/png")


# --------------------------------------------------------------------- #
# Ingestas -- lanzar y vigilar scripts/ingest_data.py y
# scripts/ingest_historical.py en segundo plano (ver jobs.py).
# --------------------------------------------------------------------- #

# Fuentes que aceptan cada script -- copiadas a mano de build_source() en
# cada script (ver scripts/ingest_data.py y scripts/ingest_historical.py)
# en vez de importarlas: importar esos scripts como modulo ejecutaria su
# codigo de nivel superior sin problema (solo definen funciones), pero
# mantener la lista aqui de forma explicita hace evidente en un solo
# sitio que fuente puede pedir esta pagina -- si un script cambia sus
# fuentes soportadas, esta lista hay que tocarla a la vez (no hay riesgo
# real de que se desincronicen en silencio: lanzar() valida contra el
# propio script mas abajo).
SIMPLE_SOURCES = [
    "nba", "wnba", "euroleague", "eurocup", "cbb", "acb", "feb", "lfendesa",
    # Añadidas 2026-09-20(b) -- ver src/scraper/feb.py, "AMPLIACION
    # 2026-09-20 (b)". Las cuatro tienen "temporada actual" en
    # scripts/ingest_data.py (opt-in, igual que feb/lfendesa).
    "lfchallenge", "lf2", "ligau", "copaespaa",
    # Añadidas 2026-09-20(c) -- ver src/scraper/feb.py, "AMPLIACION
    # 2026-09-20 (c)".
    "segundafeb", "tercerafeb",
]
# nextgen SOLO aqui (histórica), no en SIMPLE_SOURCES -- no tiene
# "temporada actual" (ver scripts/ingest_data.py build_source(), que la
# rechaza con un mensaje explícito) y siempre necesita códigos de sede
# reales via --seasons, nunca el valor por defecto de ningún script.
HISTORICAL_SOURCES = [
    "nba", "euroleague", "eurocup", "acb", "feb", "lfendesa", "wnba", "cbb", "nextgen",
    # Añadidas 2026-09-20(b). ligau esta aqui (se puede pedir con
    # --seasons explicito) pero NO en HISTORICAL_DEFAULT_SEASONS de
    # abajo -- mismo patron que nextgen (sin historico por defecto,
    # ver scripts/ingest_historical.py).
    "lfchallenge", "lf2", "ligau", "copaespaa",
    "segundafeb", "tercerafeb",
]
# Temporadas que usa scripts/ingest_historical.py cuando no se pasa
# --seasons -- copiadas de su HISTORICAL_SEASONS (ver docstring del
# script sobre por que son estas y no otras). Solo para mostrar una
# pista en el formulario; el script real decide, esto no se ejecuta.
HISTORICAL_DEFAULT_SEASONS = {
    "nba": ["2024-25", "2023-24", "2022-23", "2021-22"],
    "euroleague": ["E2025", "E2023", "E2022", "E2021"],
    "eurocup": ["U2025", "U2023", "U2022", "U2021"],
    "acb": ["2025-26", "2024-25", "2023-24", "2022-23", "2021-22"],
    "feb": ["2025", "2024", "2023", "2022", "2021"],
    # Liga Femenina Endesa -- añadida 2026-09-20 (ver src/scraper/feb.py,
    # "MULTIPLES COMPETICIONES"), misma situacion de temporada que feb.
    "lfendesa": ["2025", "2024", "2023", "2022", "2021"],
    # LF Challenge/Liga Femenina 2/Copa España -- añadidas 2026-09-20(b),
    # copiadas de scripts/ingest_historical.py HISTORICAL_SEASONS (copa
    # España SOLO 3 temporadas a proposito, ver ese docstring -- riesgo
    # real de "temporada-fantasma" confirmado en vivo para años
    # anteriores). ligau NO aparece aqui a proposito (sin historico
    # fiable todavia, mismo motivo).
    "lfchallenge": ["2025", "2024", "2023", "2022", "2021"],
    "lf2": ["2025", "2024", "2023", "2022", "2021"],
    "copaespaa": ["2026", "2025", "2024"],
    # Segunda FEB/Tercera FEB -- añadidas 2026-09-20(c). Tercera FEB
    # tiene un cambio estructural real entre temporadas (ver
    # scripts/ingest_historical.py y src/scraper/feb.py) -- 2021-2025
    # son solo una fase final pequeña, no la temporada completa.
    "segundafeb": ["2025", "2024", "2023", "2022", "2021"],
    "tercerafeb": ["2025", "2024", "2023", "2022", "2021"],
}
# Fuentes donde ya se ha chocado con un bloqueo real por ritmo de
# peticiones (ver decisiones_proyecto.md, "EUROLIGA/EUROCUP RATE LIMIT
# REAL" y el rate limit documentado de Sports Reference para cbb/wnba) --
# solo para mostrar un aviso en el formulario, no bloquea el lanzamiento
# (no hay forma fiable de saber desde aqui si el bloqueo sigue activo).
RATE_LIMITED_SOURCES = {"euroleague", "eurocup", "cbb", "wnba"}


def _back_to_ingestas(error: str):
    flash(error)
    return redirect(url_for("ingestas"))


@app.route("/ingestas")
def ingestas():
    conn = db()
    log_page = max(1, int(request.args.get("log_page", 1) or 1))
    total_logs = conn.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]
    log_page, log_pages, log_offset = paginate(total_logs, log_page)
    logs = conn.execute(
        "SELECT * FROM ingest_log ORDER BY id DESC LIMIT ? OFFSET ?",
        (PAGE_SIZE, log_offset),
    ).fetchall()
    return render_template(
        "ingestas.html",
        simple_sources=SIMPLE_SOURCES,
        historical_sources=HISTORICAL_SOURCES,
        historical_default_seasons=HISTORICAL_DEFAULT_SEASONS,
        rate_limited_sources=RATE_LIMITED_SOURCES,
        logs=logs, total_logs=total_logs, log_page=log_page, log_pages=log_pages,
    )


@app.route("/ingestas/estado.json")
def ingestas_estado():
    return jobs.status()


@app.route("/ingestas/lanzar-simple", methods=["POST"])
def lanzar_simple():
    sources = request.form.getlist("sources")
    if not sources:
        return _back_to_ingestas("Elige al menos una fuente.")
    desconocidas = [s for s in sources if s not in SIMPLE_SOURCES]
    if desconocidas:
        return _back_to_ingestas(f"Fuente no reconocida: {', '.join(desconocidas)}")

    args = ["--sources", ",".join(sources)]
    limit = (request.form.get("limit") or "").strip()
    if limit:
        if not limit.isdigit():
            return _back_to_ingestas("El límite tiene que ser un número entero.")
        args += ["--limit", limit]
    if request.form.get("no_players"):
        args.append("--no-players")

    try:
        jobs.start("ingest_data.py", args, desc=f"Ingesta simple: {', '.join(sources)}")
    except jobs.JobAlreadyRunning as exc:
        return _back_to_ingestas(str(exc))
    return redirect(url_for("ingestas"))


@app.route("/ingestas/lanzar-historica", methods=["POST"])
def lanzar_historica():
    sources = request.form.getlist("sources")
    if not sources:
        return _back_to_ingestas("Elige al menos una fuente.")
    desconocidas = [s for s in sources if s not in HISTORICAL_SOURCES]
    if desconocidas:
        return _back_to_ingestas(f"Fuente no reconocida: {', '.join(desconocidas)}")

    seasons_raw = (request.form.get("seasons") or "").strip()
    if seasons_raw and len(sources) != 1:
        return _back_to_ingestas(
            "Las temporadas personalizadas solo se pueden usar con una única "
            "fuente seleccionada -- cada fuente tiene su propio formato de "
            "código de temporada (mezclarlas sería adivinar cuál va con cuál)."
        )
    if not seasons_raw:
        sin_defecto = [s for s in sources if s not in HISTORICAL_DEFAULT_SEASONS]
        if sin_defecto:
            return _back_to_ingestas(
                f"{', '.join(sin_defecto)} no tiene temporadas por defecto -- "
                f"indica temporadas personalizadas (con una única fuente elegida)."
            )

    args = ["--sources", ",".join(sources)]
    if seasons_raw:
        args += ["--seasons", seasons_raw]
    limit = (request.form.get("limit") or "").strip()
    if limit:
        if not limit.isdigit():
            return _back_to_ingestas("El límite tiene que ser un número entero.")
        args += ["--limit", limit]
    if request.form.get("no_players"):
        args.append("--no-players")

    desc = f"Ingesta histórica: {', '.join(sources)}"
    if seasons_raw:
        desc += f" ({seasons_raw})"

    try:
        jobs.start("ingest_historical.py", args, desc=desc)
    except jobs.JobAlreadyRunning as exc:
        return _back_to_ingestas(str(exc))
    return redirect(url_for("ingestas"))


@app.route("/ingestas/detener", methods=["POST"])
def detener_ingesta():
    if not jobs.stop():
        flash("No había ninguna ingesta en marcha.")
    return redirect(url_for("ingestas"))


if __name__ == "__main__":
    # 5050 en vez del 5000 por defecto de Flask -- ese puerto suele estar
    # ocupado (en macOS, por AirPlay Receiver; en Windows, por otros
    # servicios locales). Ver docstring del modulo para cambiarlo sin
    # tocar el codigo (variable de entorno PORT).
    port = int(os.environ.get("PORT", 5050))
    # PRODUCTION=1 (AÑADIDO 2026-09-21, mismo patron que api/app.py --
    # ver su comentario de cabecera para el razonamiento completo):
    # sirve con waitress en vez del servidor de desarrollo de Flask, sin
    # el WARNING de "development server" ni el depurador interactivo de
    # Werkzeug (PIN incluido) activo. A diferencia de api/app.py, aqui
    # se sigue sirviendo SOLO en 127.0.0.1 (nunca 0.0.0.0) -- esta app
    # no esta pensada para tunel/demo (ver docstring del modulo, "SOLO
    # LECTURA... para mirar lo que ya hay guardado"), asi que no hay
    # motivo para exponerla fuera de este equipo aunque se use waitress.
    # Por defecto (sin PRODUCTION=1) el comportamiento NO cambia --
    # sigue siendo debug=True con auto-reload, para no romper el flujo
    # de trabajo habitual mientras se sigue tocando la app.
    #
    # threaded=True (rama de desarrollo) / hilos de waitress (rama de
    # produccion, parametro `threads`, default 4 de la propia libreria):
    # el panel de ingestas hace polling (una peticion cada 2s a
    # /ingestas/estado.json) mientras el navegador sigue cargando otras
    # paginas -- sin peticiones concurrentes de verdad, eso se quedaria
    # bloqueado.
    if os.environ.get("PRODUCTION") == "1":
        from waitress import serve
        # HOST (AÑADIDO 2026-10-12): por defecto sigue siendo 127.0.0.1; en la
        # Raspberry Pi el servicio systemd pone HOST=0.0.0.0 para verla en la LAN.
        host = os.environ.get("HOST", "127.0.0.1")
        print(f"Sirviendo con waitress (modo produccion, SIN depurador/auto-reload) en "
              f"http://{host}:{port}")
        from src import perf
        serve(app, host=host, port=port, threads=perf.settings()["waitress_threads"])
    else:
        app.run(debug=True, port=port, threaded=True)
