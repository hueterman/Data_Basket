"""Extractor de la Liga Endesa (ACB).

Implementa la interfaz comun definida en src/scraper/base.py
(BasketballSource). Tercer modulo de fuente del proyecto.

VERIFICADO CONTRA PAGINAS REALES (via fetch) antes de escribir el
mapeo de campos -- mismo criterio que euroleague.py -- y con varias
correcciones importantes sobre lo que documentaba el plano hasta
ahora:

- El boxscore NO vive en acb.com bajo `/partido/ver/id/{ID}` (esa ruta,
  documentada originalmente en el plano, no se pudo confirmar). El
  boxscore real vive en un SUBDOMINIO DISTINTO, `live.acb.com`, con
  URLs del tipo `live.acb.com/partidos/{slug1}-vs-{slug2}-{id}/resumen`
  (resumen corto: Min/Pts/Rt/As/Fp/+/-/Val) y
  `live.acb.com/partidos/{slug1}-vs-{slug2}-{id}/estadisticas`
  (ficha completa, la que usa este modulo). Confirmado con un partido
  real ya jugado (Real Madrid 81-71 Dreamland Gran Canaria, 5 oct
  2025): la tabla llega como HTML real (no placeholders), apta para
  `pandas.read_html`.
- `/es/liga/clasificacion` y `/es/liga/partidos` en acb.com NO
  respetan `?editionId=N` como parametro de temporada -- se probo
  explicitamente y ambas siguen mostrando la temporada 2026-27 (sin
  empezar) sea cual sea el editionId de la query. `editionId` SI
  funciona como query param en las paginas de estadisticas/lideres y
  en las fichas de jugador (`.../partidos?editionId=N`) -- confirmado
  con datos reales de Facundo Campazzo, temporada 2025-26. Esto
  contradice la asuncion original del plano de que editionId
  funcionaba de forma uniforme en toda la web.
- SUPERADO (ver docstring de `list_games()` mas abajo): `/es/liga/
  partidos` y `/es/liga/clasificacion` no soportan temporada, pero
  `/es/liga/calendario` (una ruta distinta, no explorada hasta una
  sesion posterior) SI -- via `?temporada={editionId}`, mismo valor
  numerico de editionId ya documentado aqui. `list_games()` SI esta
  implementado, para la temporada en curso Y para cualquier temporada
  historica (1983-84 en adelante).
- `/es/liga/equipos` y las fichas de equipo (`/es/liga/equipos/{slug}-
  {id}`) SI devuelven HTML real (18 equipos, plantillas completas)
  con una peticion GET simple, sin editionId -- pero por eso mismo
  reflejan siempre la plantilla ACTUAL, no la de una temporada
  historica concreta.

IDs usados en este modulo (y por que no coinciden entre fuentes, igual
que ya pasaba en euroleague.py): `team_id` es el SLUG del equipo tal y
como aparece en la URL (p. ej. "real-madrid"), NUNCA el ID numerico que
tambien aparece ahi (p. ej. "9") -- se eligio el slug porque es la
unica parte que tambien aparece en las URLs de live.acb.com
(`real-madrid-vs-dreamland-gran-canaria-104464`); el ID numerico no
aparece ahi en absoluto. `player_id` SI es el ID numerico de la ficha
de jugador (p. ej. "20201774" para Sergio Llull), tal y como aparece
en `/es/liga/jugadores/{slug}-{id}`. `game_id` es el fragmento de URL
completo de live.acb.com (p. ej.
"real-madrid-vs-dreamland-gran-canaria-104464").

AVISO IMPORTANTE -- IDs de jugador NO coinciden entre list_players() y
get_boxscore(), igual que ya pasaba en euroleague.py: la tabla de
estadisticas de live.acb.com NO trae ningun ID de jugador, solo el
nombre abreviado tal y como se muestra en pantalla (p. ej. "S. Llull",
inicial + apellido) -- no el nombre completo ("Sergio Llull") que usa
list_players(), y desde luego no el ID numerico de su ficha
(p. ej. "20201774"). Por eso PlayerStatLine.player_id en este modulo
es ese nombre abreviado, no un ID real -- cualquier pipeline que
necesite cruzar boxscore con plantilla tendra que resolverlo por
nombre con un margen de error mayor que en euroleague.py (aqui ni
siquiera el formato del nombre coincide).

`list_games(season)` -- REESCRITO en una tercera ronda de exploracion
en vivo (sesion 2026-09-09), tras encontrar una ruta que ninguna
sesion anterior habia comprobado: `/es/liga/calendario` (localizada
via `sitemap.xml`, distinta de `/es/liga/partidos`). A diferencia de
`/es/liga/partidos`/`/es/liga/clasificacion` (que ignoran cualquier
parametro de temporada, ver aviso mas arriba), esta pagina tiene un
selector de temporada REAL y funcional (combobox Radix UI, `id=
"season"`) que cubre las 43 temporadas de la ACB: 1983-84
(editionId=48) hasta 2026-27 (editionId=91) -- confirmado en vivo
seleccionando "2023-24" en el propio combobox y viendo que la URL
cambia a `?temporada=88`, el MISMO valor numerico de editionId ya
documentado arriba (editionId = año_inicio_temporada - 1935), aunque
el parametro de query se llama `temporada`, no `editionId`.

Confirmado con un fetch real de `/es/liga/calendario?temporada=88`
(sin Playwright ni navegador headless -- el HTML servido por el
servidor YA trae los 326 partidos completos de esa temporada, 34
jornadas + playoffs): resultado cruzado contra equipos/marcadores
reales de la temporada 2023-24 que ya no existen en la liga actual
(Coviran Granada, Dreamland Gran Canaria, Baxi Manresa, Zunder
Palencia), confirmando que son datos historicos genuinos. COSTE: 1
sola peticion GET por temporada para el calendario completo -- mucho
mas barato que el metodo anterior (1 + 34 peticiones), incluso para
la temporada en curso.

Estructura real del HTML (confirmada en vivo, no adivinada):

- Por jornada: `div[id^="calendar-round-"]` (una por ronda). El titulo
  vive en `[class*="RoundTitle"]` dentro de cada una: temporada
  regular = "Jornada N" (N=1..34); playoffs usan etiquetas de texto
  libre confirmadas en vivo ("Cuartos de final (1º/2º/3º)", "Semifinal
  (1º..5º)", "Final (1º..3º)") que NO encajan en el patron "Jornada
  N" -- para esas rondas `GameRef.round` queda en None (el campo es
  `int | None`, no hay donde guardar la etiqueta de texto sin inventar
  un numero).
- AVISO IMPORTANTE, confirmado en vivo: acb.com duplica CADA jornada
  dos veces en el HTML servido (mismo `id`, mismo contenido exacto --
  variante movil/escritorio, una de las dos oculta por CSS,
  `offsetParent === null` en el navegador, algo que un parseo estatico
  de HTML no puede comprobar). `list_games()` deduplica quedandose con
  la PRIMERA aparicion de cada `id` de jornada en orden de documento
  (confirmado que el contenido de ambas copias es identico byte a
  byte, asi que quedarse con la primera no pierde ningun partido).
- Dentro de cada jornada, `[class$="__days"]` agrupa los partidos por
  dia: cada hijo directo `<div>` de ese contenedor representa UN dia,
  con un `<h3 class="...dayTitle">` de fecha real en TEXTO (p. ej. "23
  de septiembre de 2023") -- NO hay ningun atributo `datetime`/
  `data-date` legible por maquina (confirmado inspeccionando los
  atributos reales del `<h3>`), asi que `list_games()` parsea el texto
  en español (nombres de mes fijos, sin libreria de fechas) a ISO
  8601.
- Por partido, dentro de cada dia: `div[class$="__roundMatch"]`
  (sufijo exacto -- OJO, un simple `[class*="__roundMatch"]` tambien
  encaja con `...__roundMatch__homeTeam`/`...__teamScore` etc., hijos
  del propio partido, y devolveria elementos equivocados). Cada
  partido trae `div[class$="__homeTeam"]`/`div[class$="__awayTeam"]`
  (usados solo para localizar el marcador de cada lado, ver mas abajo
  -- el `team_id` en si sale del enlace de live.acb.com, igual que
  antes) y un enlace real a
  `live.acb.com/partidos/{slug1}-vs-{slug2}-{game_id}/...` -- MISMO
  `game_id` que ya consume `get_boxscore()`, y confirmado que existe
  incluso para partidos futuros SIN JUGAR (sufijo `/previa` en vez del
  sufijo de partido jugado): el id se asigna de antemano, no hace
  falta esperar a que se juegue el partido para tener su `game_id`.
- PLAYED/marcador: confirmado en vivo comparando un partido ya jugado
  (temporada 2023-24: `<p class="...teamScore...">85</p>`, texto real)
  contra uno futuro sin jugar (temporada 2026-27, jornada 1, Surne
  Bilbao-Kids&Us Manresa, 26-sep-2026: `<p class="...teamScore...
  teamScore--winner"></p>` VACIO, sin texto, en AMBOS equipos) --
  `played` se determina por si el texto de `.teamScore` esta vacio o
  no, NUNCA por la clase `--winner` (esa clase aparece en los DOS
  lados cuando no hay marcador todavia; solo es fiable para marcar al
  ganador real una vez que el partido ya se jugo).

`list_games()` ya NO ignora `season`: lo convierte al editionId real
(`_edition_id_from_season()`) y pide
`/es/liga/calendario?temporada={editionId}` -- funciona igual para la
temporada en curso que para cualquier temporada historica. Como
comprobacion de cordura extra (no como fuente de verdad -- la
temporada pedida por el llamador ya es la correcta, viene de
`?temporada=`), cada partido cruza tambien su fecha real contra
`_season_label_from_date()`: si no coincide con la `season` pedida se
registra un aviso (podria indicar un caso raro no visto en vivo, como
un torneo de pretemporada fuera de las fechas habituales), pero
`GameRef.season` siempre guarda la temporada REALMENTE PEDIDA, no la
derivada.

`get_shots(game_id)` -- ACTUALIZADO 2026-10-16: YA IMPLEMENTADO via la API
api2.acb.com (ver constantes _API_BASE/_SHOTS_PATH). El parrafo siguiente
describe la conclusion ANTERIOR (solo se miraron las paginas HTML de
live.acb.com), que quedo desmentida al inspeccionar las peticiones de
red del visor de tiros.

(Historico) get_shots(game_id) estaba SIN IMPLEMENTAR, con una
confirmacion mas fuerte que en la ronda anterior: se inspecciono en
vivo tanto la vista "Jugadas" (jugada a jugada, texto plano con
sustituciones/faltas/canastas y totales corriendo, sin coordenadas)
como la vista "Avanzado" (estadisticas-avanzadas) de live.acb.com, y
se busco explicitamente cualquier campo de coordenadas
(coordX/coordY/courtX/courtY/locX/locY/shotX/shotY/shotChart/
heatmap/courtPosition) sin encontrar ninguno. Lanza AcbSourceError
explicando el porque en vez de devolver datos incorrectos o vacios
silenciosamente.

SIN CONFIRMAR EN VIVO (a diferencia de euroleague.py, que si se pudo
probar con datos de una temporada completa): la asignacion de que
tabla de `estadisticas` corresponde a que equipo se asume por el orden
en que aparecen en la pagina, coincidiendo con el orden de `game_id`
(equipo1 primero) -- no se ha verificado que ese orden sea siempre
estable.

BUGS REALES ENCONTRADOS Y CORREGIDOS tras la primera prueba en vivo
del usuario (misma disciplina que con nba.py: no se adivino la causa,
se inspecciono el DOM real con un navegador antes de tocar el codigo):

- `list_teams()`/`list_players()` devolvian nombres pegados sin
  espacio, p. ej. "ASISA JOVENTUTJOV" o "Y. KraagAlero". Causa real:
  la tarjeta de equipo/jugador tiene el nombre y un segundo dato
  (siglas del equipo, o posicion del jugador) en dos elementos HTML
  hermanos dentro del mismo `<a>`, y `get_text(strip=True)` sobre todo
  el enlace concatena ambos sin separador. Corregido apuntando al
  elemento correcto: `h3` para el nombre de equipo (dentro de
  `a[data-testid='team-card-link']`, ignorando el enlace duplicado del
  menu de navegacion que no lleva texto), y al primer `<p>` para el
  nombre de jugador (el segundo `<p>` es la posicion, p. ej. "Alero",
  y se descarta).
- `get_boxscore()` devolvia TeamStatLine con todo el tiro a 0 y
  rebotes/valoracion absurdamente bajos (p. ej. val=3 para un equipo
  entero). Causa real, confirmada inspeccionando la tabla real: cada
  tabla de estadisticas tiene DOS filas especiales al final, "Equipo"
  (una fila de estadisticas de equipo sin jugador asociado, con tiro
  siempre a 0/0 y muy pocos rebotes/perdidas) y "Totales" (los
  totales reales del equipo) -- el filtro de deteccion de fila de
  totales incluia "equipo" como sinonimo de "totales" y, al coger la
  PRIMERA fila que encajaba, se quedaba con "Equipo" en vez de con
  "Totales" (aparece antes en la tabla). Corregido: "Equipo" sigue
  excluyendose de las filas de jugador (no es un jugador), pero ya NO
  se usa como fuente de los totales de equipo -- solo "Totales"/
  "Total" cuentan para eso. Ademas se detecto que live.acb.com a veces
  duplica la tabla completa de un equipo (mismo contenido exacto en
  dos `<table>` de la respuesta) -- `get_boxscore()` ahora deduplica
  las tablas por contenido antes de emparejarlas con slug1/slug2, en
  vez de asumir que las dos primeras tablas encontradas son siempre
  las dos correctas.
- La celda "Jugador" de cada fila de jugador incluye el dorsal pegado
  al nombre (p. ej. "33  G. Grinvalds", con doble espacio), no solo el
  nombre abreviado que describia el aviso de arriba. `player_id`/
  `player_name` ahora se limpian quitando ese prefijo numerico, para
  quedarse con el mismo formato "S. Llull" ya documentado.
"""

from __future__ import annotations

import io
import logging
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.scraper.base import (
    BoxscoreData,
    GameRef,
    Player,
    PlayerStatLine,
    Shot,
    Team,
    TeamStatLine,
)

logger = logging.getLogger(__name__)

_ACB_BASE = "https://www.acb.com"
_LIVE_BASE = "https://live.acb.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_DEFAULT_TIMEOUT = 30

# --- API de coordenadas de tiro (AÑADIDO 2026-10-16) -----------------------
# Descubierta via DevTools en live.acb.com (peticion `match-shots`) y
# contrastada con el paquete de R BAwiR (do_scrape_shots_acb, G. Vinue), que
# usa el mismo endpoint. Contradice la conclusion previa de este modulo
# ("ACB no expone coordenadas"): las pantallas HTML de live.acb.com no las
# muestran, pero la API que alimenta el visor de tiros SI las sirve.
# Requiere cabecera X-APIKEY (la clave publica de la propia web) -> se lee de
# la variable de entorno ACB_API_KEY o de .env en la raiz del proyecto.
_API_BASE = "https://api2.acb.com"
_SHOTS_PATH = "/api/matchdata/MatchShots/match-shots"

# playType de la API -> (tipo, anotado). 92/96 (tiros libres) y 533 se
# descartan, igual que hace BAwiR. 100 = mate (anotado, cuenta como T2).
_FIELD_GOAL_PLAY_TYPES = {
    93: ("2", True), 94: ("3", True), 97: ("2", False), 98: ("3", False),
    100: ("2", True),
}
_ENV_KEY_NAME = "ACB_API_KEY"

# Limites de pista en la unidad nativa (mm). La pista FIBA mide 28 m x 15 m, asi
# que |posY| <= 7500 y 0 <= posX <= 28000 (las coordenadas van reflejadas sobre
# un unico aro, de modo que los tiros de fondo de pista llegan a x ~ 25000 y SE
# CONSERVAN). Se deja un margen por el error de captura manual. Lo que cae fuera
# es un dato corrupto (p. ej. posY = 23330 visto en el partido 105388) y se
# descarta con un aviso en el log, nunca en silencio.
_MAX_ABS_Y_MM = 7600
_MAX_X_MM = 28500

# game_id = "{slug1}-vs-{slug2}-{numero}", tal y como lo usa live.acb.com.
_GAME_ID_RE = re.compile(r"^(?P<slug1>[a-z0-9-]+)-vs-(?P<slug2>[a-z0-9-]+)-(?P<numero>\d+)$")

# Enlace real de una tarjeta de partido en /es/liga/partidos hacia
# live.acb.com -- mismo formato que _GAME_ID_RE pero anclado al
# principio de una URL completa en vez de a un game_id suelto (ver
# docstring del modulo, list_games()).
_LIVE_LINK_RE = re.compile(
    r"^https://live\.acb\.com/partidos/(?P<slug1>[a-z0-9-]+)-vs-(?P<slug2>[a-z0-9-]+)-(?P<numero>\d+)/"
)

# Columnas confirmadas en vivo de live.acb.com/partidos/.../estadisticas
# (ver docstring del modulo). "T2"/"T3"/"TL" llegan como "hechos/intentados".
_MADE_ATT_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")

# season "YYYY-YY" (p. ej. "2023-24", "2026-27") -> editionId real de
# acb.com (ver docstring del modulo, list_games()): editionId =
# año_inicio_temporada - 1935, confirmado en vivo con el selector de
# temporada de /es/liga/calendario (1983-84 = editionId 48, 2026-27 =
# editionId 91).
_SEASON_RE = re.compile(r"^\s*(?P<start>\d{4})-(?P<end>\d{2})\s*$")
_EDITION_ID_BASE_YEAR = 1935

# Titulo de ronda de temporada regular en /es/liga/calendario, p. ej.
# "Jornada 1" (ver docstring del modulo) -- las rondas de playoff usan
# otras etiquetas ("Cuartos de final (1º)", "Semifinal (2º)"...) que no
# encajan aqui a proposito (round queda en None para esas, ver GameRef).
_ROUND_NUMBER_RE = re.compile(r"Jornada\s+(\d+)", re.IGNORECASE)

# Cabecera de dia en /es/liga/calendario, texto en español sin ningun
# atributo de fecha legible por maquina (confirmado en vivo -- ver
# docstring del modulo), p. ej. "23 de septiembre de 2023".
_DAY_HEADER_RE = re.compile(
    r"(?P<day>\d{1,2})\s+de\s+(?P<month>[a-zA-ZáéíóúñÁÉÍÓÚÑ]+)\s+de\s+(?P<year>\d{4})"
)
_SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def _edition_id_from_season(season: str) -> int:
    """"2023-24" -> 88. Lanza AcbSourceError si `season` no tiene el
    formato "YYYY-YY" esperado -- mejor fallar con un mensaje claro que
    adivinar un editionId incorrecto y devolver la temporada
    equivocada en silencio (mismo criterio "avisa y no adivina" del
    resto del proyecto)."""
    match = _SEASON_RE.match(str(season)) if season else None
    if not match:
        raise AcbSourceError(
            f"season={season!r} no tiene el formato esperado 'YYYY-YY' "
            f"(p. ej. '2023-24', '2026-27') -- se necesita para calcular "
            f"el editionId real de acb.com (editionId = "
            f"año_inicio_temporada - {_EDITION_ID_BASE_YEAR}, ver "
            f"docstring del modulo)."
        )
    start = int(match.group("start"))
    expected_end = (start + 1) % 100
    if int(match.group("end")) != expected_end:
        # Una etiqueta como "2011-10" calcula bien el editionId (usa solo el
        # año de inicio) pero etiqueta TODOS los partidos con una temporada
        # que no existe. Mejor parar que guardar datos con la season mal.
        raise AcbSourceError(
            f"season={season!r} no es una temporada valida: tras "
            f"{start} deberia ir '{start}-{expected_end:02d}'."
        )
    return start - _EDITION_ID_BASE_YEAR


def _round_number_from_title(title: str) -> int | None:
    match = _ROUND_NUMBER_RE.search(title or "")
    return int(match.group(1)) if match else None


def _parse_spanish_day_header(text: str) -> str | None:
    """"23 de septiembre de 2023" -> "2023-09-23". Devuelve None (nunca
    adivina) si el texto no encaja con el patron esperado o si el mes
    no esta en `_SPANISH_MONTHS`."""
    match = _DAY_HEADER_RE.search(text or "")
    if not match:
        return None
    month = _SPANISH_MONTHS.get(match.group("month").strip().lower())
    if month is None:
        return None
    return f"{int(match.group('year')):04d}-{month:02d}-{int(match.group('day')):02d}"


class AcbSourceError(RuntimeError):
    """Fallo al extraer datos de acb.com/live.acb.com, con contexto de
    que peticion concreta ha fallado y por que -- mismo estandar de
    codigo del proyecto que NbaSourceError/EuroleagueSourceError:
    identificar el error concreto y relanzarlo con contexto, en vez de
    dejar que una excepcion generica pare la ejecucion en seco. Se usa
    tambien para las dos limitaciones documentadas arriba
    (list_games/get_shots sin endpoint confirmado), para que quien
    llame pueda distinguir "no implementado todavia" de un fallo de
    red real."""


def _made_attempted(raw: str) -> tuple[int, int]:
    """"3/5" -> (3, 5). Nunca lanza: si no encaja, registra un aviso y
    devuelve (0, 0) en vez de tirar abajo el resto de la fila."""
    match = _MADE_ATT_RE.match(str(raw))
    if not match:
        logger.warning("Formato de tiro no reconocido: %r; se registra como (0, 0)", raw)
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def _to_int(raw: object, default: int = 0) -> int:
    try:
        text = str(raw).strip()
        if not text or text in {"-", "—"}:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


# La celda "Jugador" de una fila de jugador real trae el dorsal pegado
# al nombre, p. ej. "33  G. Grinvalds" (confirmado en vivo -- ver
# docstring del modulo). Se quita ese prefijo para quedarse con el
# formato "S. Llull" que documenta el resto del modulo.
_JERSEY_PREFIX_RE = re.compile(r"^\d+\s+")


def _clean_player_cell(raw: object) -> str:
    return _JERSEY_PREFIX_RE.sub("", str(raw).strip()).strip()


# Reintentos HTTP (AÑADIDO 2026-10-13): acb.com devolvio un 502 Bad Gateway
# puntual en plena ingesta semanal y, sin reintentos, un solo fallo transitorio
# tumbaba la fuente ENTERA (volvio a funcionar al repetir minutos despues). Se
# reintenta SOLO lo que suele ser transitorio -- errores de conexion/timeout y
# 429/500/502/503/504 -- con espera creciente (2 s, 4 s, 8 s). Un 404 u otro
# 4xx NO se reintenta: es un fallo real, no un hipo del servidor.
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2.0
_RETRY_MAX_DELAY = 30.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class AcbSource:
    source = "acb"

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # -- infraestructura HTTP ---------------------------------------------

    @staticmethod
    def _retry_delay(attempt: int, response=None) -> float:
        """Espera antes del reintento numero `attempt+1`: exponencial, o el
        `Retry-After` del servidor si lo manda en segundos (con tope)."""
        delay = _RETRY_BACKOFF_BASE * (2 ** attempt)
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.strip().isdigit():
                delay = max(delay, float(retry_after))
        return min(delay, _RETRY_MAX_DELAY)

    def _get(self, url: str, headers: dict | None = None,
             params: dict | None = None) -> requests.Response:
        """GET con reintentos (ver _MAX_RETRIES). Lanza requests.RequestException
        si se agotan o si el error no es transitorio -- los llamadores ya lo
        envuelven en AcbSourceError."""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._session.get(
                    url, headers=headers, params=params, timeout=_DEFAULT_TIMEOUT)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= _MAX_RETRIES:
                    raise
                delay = self._retry_delay(attempt)
                logger.warning("ACB: %s (%s) -- reintento %d/%d en %.0fs",
                               url, type(exc).__name__, attempt + 1, _MAX_RETRIES, delay)
                time.sleep(delay)
                continue
            if response.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES:
                delay = self._retry_delay(attempt, response)
                logger.warning("ACB: %s devolvio HTTP %d -- reintento %d/%d en %.0fs",
                               url, response.status_code, attempt + 1, _MAX_RETRIES, delay)
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response
        raise AssertionError("inalcanzable")  # el bucle siempre retorna o lanza

    def _get_soup(self, url: str, context: str) -> BeautifulSoup:
        try:
            response = self._get(url)
        except requests.RequestException as exc:
            raise AcbSourceError(f"Fallo pidiendo {context}: {exc}") from exc
        return BeautifulSoup(response.text, "html.parser")

    # -- equipos / jugadores ------------------------------------------------

    def list_teams(self, season: str) -> list[Team]:
        """Devuelve los equipos ACTUALES de la Liga Endesa (ver aviso
        del docstring del modulo: `/es/liga/equipos` no distingue
        temporada). `season` se acepta por la interfaz comun pero no
        tiene efecto todavia -- se ignora explicitamente, no en
        silencio: se registra un aviso si no es None."""
        if season:
            logger.warning(
                "AcbSource.list_teams(season=%r): /es/liga/equipos no "
                "distingue temporada via URL (ver docstring); se "
                "devuelven los equipos actuales, no los de esa season.",
                season,
            )
        soup = self._get_soup(f"{_ACB_BASE}/es/liga/equipos", context="listado de equipos")

        teams: dict[str, str] = {}
        try:
            # Solo las tarjetas de equipo reales (data-testid='team-card-link')
            # llevan el nombre en un <h3> -- el enlace del menu de navegacion
            # con el mismo href no tiene texto (ver docstring del modulo).
            for link in soup.select("a[data-testid='team-card-link'][href*='/es/liga/equipos/']"):
                href = link.get("href", "")
                match = re.search(r"/es/liga/equipos/([a-z0-9-]+)-(\d+)", href)
                if not match:
                    continue
                slug = match.group(1)
                name_tag = link.find("h3")
                name = (name_tag.get_text(strip=True) if name_tag else link.get_text(strip=True)) or slug
                if name:
                    teams[slug] = name
        except AttributeError as exc:
            raise AcbSourceError(
                f"Forma inesperada en /es/liga/equipos al extraer equipos: {exc}"
            ) from exc

        if not teams:
            raise AcbSourceError(
                "No se encontro ningun equipo en /es/liga/equipos -- "
                "es probable que la pagina haya cambiado de marcado."
            )
        return [Team(team_id=slug, name=name, source=self.source) for slug, name in teams.items()]

    def list_players(self, team_id: str) -> list[Player]:
        """Plantilla ACTUAL de `team_id` (slug, ver docstring del
        modulo). Resuelve primero el ID numerico real del equipo
        consultando /es/liga/equipos (los dos modulos comparten slug,
        pero solo esa pagina expone el ID numerico que necesita la URL
        de la ficha de equipo)."""
        team_url = self._resolve_team_url(team_id)
        soup = self._get_soup(team_url, context=f"ficha de equipo team_id={team_id!r}")

        players: dict[str, str] = {}
        try:
            for link in soup.select("a[href*='/es/liga/jugadores/']"):
                href = link.get("href", "")
                match = re.search(r"/es/liga/jugadores/([a-z0-9-]+)-(\d+)", href)
                if not match:
                    continue
                player_id = match.group(2)
                # La tarjeta de jugador tiene dos <p>: el primero es el
                # nombre, el segundo la posicion (p. ej. "Alero") -- coger
                # solo el primero evita el bug de nombre+posicion pegados
                # (ver docstring del modulo).
                name_tag = link.find("p")
                name = name_tag.get_text(strip=True) if name_tag else link.get_text(strip=True)
                if name:
                    players[player_id] = name
        except AttributeError as exc:
            raise AcbSourceError(
                f"Forma inesperada en la ficha de team_id={team_id!r} al "
                f"extraer jugadores: {exc}"
            ) from exc

        return [
            Player(player_id=pid, name=name, team_id=team_id, source=self.source)
            for pid, name in players.items()
        ]

    def _resolve_team_url(self, team_id: str) -> str:
        soup = self._get_soup(f"{_ACB_BASE}/es/liga/equipos", context="listado de equipos")
        for link in soup.select("a[href*='/es/liga/equipos/']"):
            href = link.get("href", "")
            if f"/es/liga/equipos/{team_id}-" in href:
                return href if href.startswith("http") else f"{_ACB_BASE}{href}"
        raise AcbSourceError(
            f"No se encontro team_id={team_id!r} en /es/liga/equipos -- "
            f"revisa que el slug sea correcto (viene de list_teams())."
        )

    # -- partidos -----------------------------------------------------------

    @staticmethod
    def _season_label_from_date(date_iso: str | None) -> str:
        """Deriva la etiqueta de temporada tipo "2026-27" a partir de la
        fecha REAL de un partido. Ya NO es la fuente de verdad de
        `GameRef.season` (list_games() ahora sabe la temporada real
        porque la pidio explicitamente via `?temporada=`, ver docstring
        del modulo) -- se mantiene como comprobacion de cordura extra
        sobre la fecha de cada partido. La Liga Endesa corre de
        septiembre a junio (igual que la NBA de octubre a abril, mismo
        patron ya usado en nba.py): un partido en mes >= 7 (jul-dic)
        pertenece a la temporada que EMPIEZA ese año; un partido en mes
        < 7 (ene-jun) pertenece a la temporada que empezo el año
        anterior. Si no hay fecha confirmada devuelve "" en vez de
        adivinar."""
        if not date_iso:
            return ""
        try:
            year, month = int(date_iso[:4]), int(date_iso[5:7])
        except (ValueError, IndexError):
            return ""
        start_year = year if month >= 7 else year - 1
        return f"{start_year}-{(start_year + 1) % 100:02d}"

    def _parse_match_div(
        self, match_div, round_number: int | None, date_iso: str | None, season: str
    ) -> GameRef | None:
        """Un `div[class$='__roundMatch']` -> GameRef, o None si no
        trae un enlace reconocible a live.acb.com (nunca deberia
        pasar en un partido real, pero se avisa y se salta en vez de
        lanzar y tirar abajo toda la temporada por una tarjeta rara)."""
        live_link = match_div.select_one("a[href^='https://live.acb.com/partidos/']")
        if live_link is None:
            logger.warning(
                "Tarjeta de partido sin enlace a live.acb.com reconocible "
                "(round=%r, date=%r) -- se omite.", round_number, date_iso,
            )
            return None
        href = live_link.get("href", "")
        link_match = _LIVE_LINK_RE.match(href)
        if not link_match:
            logger.warning("Enlace a live.acb.com con formato inesperado: %r -- se omite.", href)
            return None
        slug1, slug2, numero = (
            link_match.group("slug1"), link_match.group("slug2"), link_match.group("numero")
        )
        game_id = f"{slug1}-vs-{slug2}-{numero}"

        home_div = match_div.select_one("div[class$='__homeTeam']")
        away_div = match_div.select_one("div[class$='__awayTeam']")
        home_score_text = self._team_score_text(home_div)
        away_score_text = self._team_score_text(away_div)
        # played se decide por si el marcador tiene texto o no -- la
        # clase CSS "--winner" aparece en AMBOS lados cuando el partido
        # todavia no se ha jugado, no sirve para esto (ver docstring
        # del modulo).
        played = bool(home_score_text) and bool(away_score_text)
        home_score = _to_int(home_score_text) if played else None
        away_score = _to_int(away_score_text) if played else None

        expected_season = self._season_label_from_date(date_iso)
        if expected_season and expected_season != season:
            logger.warning(
                "game_id=%s: la temporada derivada de su fecha (%s -> %s) "
                "no coincide con la season pedida (%s, editionId=%s) -- "
                "se guarda la season PEDIDA en GameRef.season, revisar la "
                "formula de editionId si esto se repite.",
                game_id, date_iso, expected_season, season, _edition_id_from_season(season),
            )

        return GameRef(
            game_id=game_id,
            source=self.source,
            season=season,
            round=round_number,
            date=date_iso,
            home_team_id=slug1,
            away_team_id=slug2,
            home_score=home_score,
            away_score=away_score,
            played=played,
        )

    @staticmethod
    def _team_score_text(team_div) -> str:
        if team_div is None:
            return ""
        score_el = team_div.select_one("p[class*='teamScore']")
        return score_el.get_text(strip=True) if score_el else ""

    def list_games(self, season: str) -> list[GameRef]:
        """Enumera todos los partidos de `season` (formato "YYYY-YY",
        p. ej. "2023-24" o "2026-27") pidiendo UNA sola vez
        `/es/liga/calendario?temporada={editionId}` -- ver docstring
        del modulo para la investigacion completa que encontro esta
        ruta y su estructura real de HTML. Funciona igual para
        cualquier temporada desde 1983-84 (editionId=48) hasta la
        temporada en curso."""
        edition_id = _edition_id_from_season(season)
        url = f"{_ACB_BASE}/es/liga/calendario?temporada={edition_id}"
        soup = self._get_soup(
            url, context=f"calendario de season={season!r} (temporada={edition_id})"
        )

        games: list[GameRef] = []
        seen_round_ids: set[str] = set()
        for round_div in soup.select("div[id^='calendar-round-']"):
            round_id = round_div.get("id")
            # acb.com duplica cada jornada dos veces en el HTML servido
            # (variante movil/escritorio, contenido identico -- ver
            # docstring del modulo): se queda solo con la primera
            # aparicion de cada id de jornada en orden de documento.
            if round_id in seen_round_ids:
                continue
            seen_round_ids.add(round_id)

            title_el = round_div.select_one("[class*='RoundTitle']")
            round_number = _round_number_from_title(
                title_el.get_text(strip=True) if title_el else ""
            )

            days_container = round_div.select_one("[class$='__days']")
            if days_container is None:
                logger.warning(
                    "round_id=%s: sin contenedor de dias reconocible -- se omite "
                    "esta jornada.", round_id,
                )
                continue

            for day_div in days_container.find_all("div", recursive=False):
                day_header = day_div.find("h3")
                date_iso = (
                    _parse_spanish_day_header(day_header.get_text(strip=True))
                    if day_header is not None else None
                )
                if day_header is not None and date_iso is None:
                    logger.warning(
                        "round_id=%s: no se pudo parsear la fecha de cabecera "
                        "de dia %r -- los partidos de este dia quedan con "
                        "date=None.", round_id, day_header.get_text(strip=True),
                    )
                for match_div in day_div.select("div[class$='__roundMatch']"):
                    game = self._parse_match_div(match_div, round_number, date_iso, season)
                    if game is not None:
                        games.append(game)

        if not games:
            raise AcbSourceError(
                f"No se encontro ningun partido en {url} -- puede que la "
                f"temporada {season!r} (editionId={edition_id}) no exista "
                f"o no tenga calendario publicado, o que el marcado de "
                f"/es/liga/calendario haya cambiado (ver docstring del "
                f"modulo)."
            )
        return games

    # -- boxscore / tiros -----------------------------------------------------

    def _split_game_id(self, game_id: str) -> tuple[str, str, str]:
        match = _GAME_ID_RE.match(game_id)
        if not match:
            raise AcbSourceError(
                f"game_id={game_id!r} no tiene el formato esperado "
                f"'equipo1-vs-equipo2-numero' (el que usa live.acb.com)."
            )
        return match.group("slug1"), match.group("slug2"), match.group("numero")

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        slug1, slug2, _numero = self._split_game_id(game_id)
        url = f"{_LIVE_BASE}/partidos/{game_id}/estadisticas"
        try:
            response = self._get(url)
            tables = pd.read_html(io.StringIO(response.text))
        except requests.RequestException as exc:
            raise AcbSourceError(f"Fallo pidiendo estadisticas de game_id={game_id!r}: {exc}") from exc
        except ValueError as exc:
            raise AcbSourceError(
                f"No se encontro ninguna tabla HTML en la ficha de "
                f"game_id={game_id!r}: {exc}"
            ) from exc

        candidate_tables = [df for df in tables if "Jugador" in df.columns]
        # live.acb.com a veces repite la tabla completa de un equipo dos
        # veces en la misma respuesta (mismo contenido exacto) -- confirmado
        # en vivo. Se deduplica por contenido antes de emparejar con
        # slug1/slug2, en vez de asumir que las dos primeras encontradas
        # son siempre las dos correctas.
        seen_csv: set[str] = set()
        stat_tables = []
        for df in candidate_tables:
            key = df.to_csv(index=False)
            if key in seen_csv:
                continue
            seen_csv.add(key)
            stat_tables.append(df)

        if len(stat_tables) < 2:
            raise AcbSourceError(
                f"Se esperaban 2 tablas de estadisticas (una por equipo, "
                f"tras deduplicar) en game_id={game_id!r}, se encontraron "
                f"{len(stat_tables)}. El marcado de live.acb.com puede "
                f"haber cambiado."
            )

        team_stats: list[TeamStatLine] = []
        player_stats: list[PlayerStatLine] = []
        for team_id, df in zip((slug1, slug2), stat_tables[:2]):
            try:
                jugador_lower = df["Jugador"].astype(str).str.strip().str.lower()
                # "Equipo" y "Totales"/"Total" son dos filas ESPECIALES
                # distintas (confirmado en vivo -- ver docstring del
                # modulo): "Equipo" son estadisticas sin jugador asociado
                # (tiro siempre 0/0), "Totales" son los totales reales del
                # equipo. Ambas se excluyen de las filas de jugador, pero
                # SOLO "Totales"/"Total" cuenta como fuente de los totales
                # de equipo -- coger la primera fila que encajara con
                # {"total","totales","equipo"} devolvia "Equipo" por error,
                # porque aparece antes en la tabla.
                non_player_mask = jugador_lower.isin({"total", "totales", "equipo"})
                real_totals_mask = jugador_lower.isin({"total", "totales"})
                totals_row = df[real_totals_mask]
                player_rows = df[~non_player_mask]

                t2_made_sum = t2_att_sum = t3_made_sum = t3_att_sum = 0
                ft_made_sum = ft_att_sum = ro_sum = rd_sum = as_sum = 0
                per_sum = rec_sum = tap_sum = tr_sum = fp_sum = fr_sum = val_sum = 0

                for _, row in player_rows.iterrows():
                    if str(row.get("Jugador", "")).strip() in {"", "nan"}:
                        continue
                    t2_made, t2_att = _made_attempted(row.get("T2", "0/0"))
                    t3_made, t3_att = _made_attempted(row.get("T3", "0/0"))
                    ft_made, ft_att = _made_attempted(row.get("TL", "0/0"))
                    minutes_raw = str(row.get("Min", "")).strip()
                    val = _to_int(row.get("Val"))

                    t2_made_sum += t2_made; t2_att_sum += t2_att
                    t3_made_sum += t3_made; t3_att_sum += t3_att
                    ft_made_sum += ft_made; ft_att_sum += ft_att
                    ro_sum += _to_int(row.get("RO")); rd_sum += _to_int(row.get("RD"))
                    as_sum += _to_int(row.get("As")); per_sum += _to_int(row.get("Pér"))
                    rec_sum += _to_int(row.get("Rec")); tap_sum += _to_int(row.get("Tap"))
                    tr_sum += _to_int(row.get("TR")); fp_sum += _to_int(row.get("Fp"))
                    fr_sum += _to_int(row.get("Fr")); val_sum += val

                    if minutes_raw and minutes_raw not in {"-", "nan", "0:00"}:
                        player_cell = _clean_player_cell(row.get("Jugador", ""))
                        player_stats.append(
                            PlayerStatLine(
                                game_id=game_id,
                                player_id=player_cell,
                                player_name=player_cell,
                                team_id=team_id,
                                minutes=_parse_minutes(minutes_raw),
                                points=_to_int(row.get("Pts")),
                                rebounds=_to_int(row.get("RT")),
                                assists=_to_int(row.get("As")),
                                val=val,
                                plus_minus=_to_int(row.get("+/-"), default=None) if "+/-" in df.columns else None,
                            )
                        )

                if not totals_row.empty:
                    trow = totals_row.iloc[0]
                    t2_made, t2_att = _made_attempted(trow.get("T2", "0/0"))
                    t3_made, t3_att = _made_attempted(trow.get("T3", "0/0"))
                    ft_made, ft_att = _made_attempted(trow.get("TL", "0/0"))
                    team_stats.append(
                        TeamStatLine(
                            game_id=game_id, team_id=team_id,
                            t2_made=t2_made, t2_att=t2_att,
                            t3_made=t3_made, t3_att=t3_att,
                            ft_made=ft_made, ft_att=ft_att,
                            reb_off=_to_int(trow.get("RO")), reb_def=_to_int(trow.get("RD")),
                            ast=_to_int(trow.get("As")), stl=_to_int(trow.get("Rec")),
                            turnovers=_to_int(trow.get("Pér")), blocks=_to_int(trow.get("Tap")),
                            fouls=_to_int(trow.get("Fp")), val=_to_int(trow.get("Val")),
                        )
                    )
                else:
                    logger.warning(
                        "game_id=%s, team_id=%s: sin fila de totales explicita "
                        "en la tabla; se calcula sumando las filas de "
                        "jugador (sin verificar contra el dato oficial).",
                        game_id, team_id,
                    )
                    team_stats.append(
                        TeamStatLine(
                            game_id=game_id, team_id=team_id,
                            t2_made=t2_made_sum, t2_att=t2_att_sum,
                            t3_made=t3_made_sum, t3_att=t3_att_sum,
                            ft_made=ft_made_sum, ft_att=ft_att_sum,
                            reb_off=ro_sum, reb_def=rd_sum,
                            ast=as_sum, stl=rec_sum,
                            turnovers=per_sum, blocks=tap_sum,
                            fouls=fp_sum, val=val_sum,
                        )
                    )
            except KeyError as exc:
                raise AcbSourceError(
                    f"Tabla de estadisticas con columnas inesperadas para "
                    f"game_id={game_id!r}, team_id={team_id!r}: {exc}"
                ) from exc

        return BoxscoreData(game_id=game_id, team_stats=team_stats, player_stats=player_stats)

    def get_shots(self, game_id: str) -> list[Shot]:
        """Coordenadas de tiro de un partido via api2.acb.com (ver
        constantes _API_BASE/_SHOTS_PATH arriba).

        Unidad: la NATIVA de la API, sin convertir (igual que euroleague.py;
        la normalizacion a metros es cosa de src/pipeline/, ver la entrada
        "acb" de SHOT_COORD_CALIBRATION en src/features.py). CONFIRMADO con
        datos reales (2026-10-16): loc_x/loc_y son MILIMETROS con el ORIGEN
        EN EL ARO (no en la linea de fondo, como decia la documentacion de
        BAwiR): loc_x = distancia al aro a lo largo de la pista (recortada a
        0 detras del plano del aro), loc_y = eje lateral centrado. Todos los
        tiros vienen reflejados sobre un solo aro; los de fondo de pista
        llegan a loc_x ~ 25000 y se conservan. Se descartan (con aviso en el
        log) los tiros con |loc_y| > 7600 o loc_x fuera de 0-28500, que son
        datos corruptos.

        `player_id` es el ID de licencia ACB (numerico), distinto del
        nombre abreviado que usa get_boxscore() -- ver aviso en el
        docstring del modulo; hay que cruzarlos por nombre/plantilla.

        Solo intentos de campo (2/3 puntos y mates). Si el JSON no trae la
        forma esperada se lanza AcbSourceError indicando que claves llegaron
        (nunca se devuelve una lista vacia en silencio)."""
        slug1, slug2, numero = self._split_game_id(game_id)
        api_key = _load_api_key()
        headers = {
            "Accept": "*/*",
            "Origin": _LIVE_BASE,
            "Referer": f"{_LIVE_BASE}/",
            "X-APIKEY": api_key,
        }
        try:
            response = self._get(
                f"{_API_BASE}{_SHOTS_PATH}", headers=headers, params={"matchId": numero}
            )
            payload = response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            hint = (" -- la X-APIKEY puede haber caducado: volver a copiarla de "
                    "DevTools (ver .env)") if status in (401, 403) else ""
            raise AcbSourceError(
                f"Fallo pidiendo tiros de game_id={game_id!r} (matchId={numero}): {exc}{hint}"
            ) from exc
        except ValueError as exc:
            raise AcbSourceError(
                f"La respuesta de match-shots de game_id={game_id!r} no es JSON: {exc}"
            ) from exc

        return _parse_shots_payload(payload, game_id, slug1, slug2)


def _load_api_key() -> str:
    """X-APIKEY de ACB: variable de entorno ACB_API_KEY o, si no, la linea
    ACB_API_KEY=... de `.env` en la raiz del proyecto. Nunca se escribe en
    el codigo ni en el repositorio."""
    key = os.environ.get(_ENV_KEY_NAME, "").strip()
    if not key:
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                name, sep, value = line.partition("=")
                if sep and name.strip() == _ENV_KEY_NAME:
                    key = value.strip().strip("\"'")
                    break
        except OSError:
            pass
    if not key:
        raise AcbSourceError(
            f"Falta la clave de la API de ACB: define la variable de entorno "
            f"{_ENV_KEY_NAME} o añade la linea {_ENV_KEY_NAME}=... a .env "
            f"(se copia de DevTools -> Network -> match-shots -> X-Apikey)."
        )
    return key


def _norm_key(name: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _pick(record: dict, *candidates: str):
    """Valor de la primera clave de `candidates` presente en `record`,
    ignorando mayusculas y separadores (posX == pos_x == POS_X)."""
    normalized = {_norm_key(k): v for k, v in record.items()}
    for cand in candidates:
        value = normalized.get(_norm_key(cand))
        if value is not None:
            return value
    return None


def _truthy(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0"}:
        return value.strip().lower() in {"true", "1"}
    return None


def _parse_shots_payload(payload: object, game_id: str, home_slug: str, away_slug: str) -> list[Shot]:
    if not isinstance(payload, dict):
        raise AcbSourceError(
            f"match-shots de game_id={game_id!r}: se esperaba un objeto JSON, "
            f"llego {type(payload).__name__}."
        )
    points = _pick(payload, "shotPoints")
    if not isinstance(points, list):
        raise AcbSourceError(
            f"match-shots de game_id={game_id!r}: no hay lista 'shotPoints'. "
            f"Claves recibidas: {sorted(payload.keys())}"
        )

    # licencia de jugador -> lado (local/visitante), por si el punto de tiro
    # no trae su propio indicador de local.
    side_by_license: dict[str, bool] = {}
    for key, is_home in (("homePlayerStats", True), ("awayPlayerStats", False)):
        stats = _pick(payload, key)
        if isinstance(stats, list):
            for rec in stats:
                if isinstance(rec, dict):
                    lic = _pick(rec, "playerLicenseId", "licenseId", "license")
                    if lic is not None:
                        side_by_license[str(lic)] = is_home

    shots: list[Shot] = []
    skipped_non_fg = 0
    skipped_out_of_court = 0
    for i, point in enumerate(points):
        if not isinstance(point, dict):
            raise AcbSourceError(f"game_id={game_id!r}: shotPoints[{i}] no es un objeto.")
        try:
            play_type = int(_pick(point, "playType"))
        except (TypeError, ValueError):
            raise AcbSourceError(
                f"game_id={game_id!r}: shotPoints[{i}] sin playType numerico. "
                f"Claves: {sorted(point.keys())}"
            ) from None
        if play_type not in _FIELD_GOAL_PLAY_TYPES:
            skipped_non_fg += 1
            continue
        shot_type, made = _FIELD_GOAL_PLAY_TYPES[play_type]

        x, y = _pick(point, "posX"), _pick(point, "posY")
        lic = _pick(point, "playerLicenseId", "licenseId", "license")
        if x is None or y is None or lic is None:
            raise AcbSourceError(
                f"game_id={game_id!r}: shotPoints[{i}] sin posX/posY/licencia de "
                f"jugador. Claves: {sorted(point.keys())}"
            )

        try:
            x_mm, y_mm = float(x), float(y)
        except (TypeError, ValueError):
            raise AcbSourceError(
                f"game_id={game_id!r}: shotPoints[{i}] con posX/posY no numericos: "
                f"{x!r}, {y!r}"
            ) from None
        if not (0 <= x_mm <= _MAX_X_MM) or abs(y_mm) > _MAX_ABS_Y_MM:
            skipped_out_of_court += 1
            continue

        is_home = _truthy(_pick(point, "local", "isLocal", "isHome"))
        if is_home is None:
            is_home = side_by_license.get(str(lic))
        if is_home is None:
            raise AcbSourceError(
                f"game_id={game_id!r}: no se pudo saber si el tiro de la licencia "
                f"{lic} es local o visitante (ni 'local' en el tiro ni la "
                f"licencia en homePlayerStats/awayPlayerStats). Claves del tiro: "
                f"{sorted(point.keys())}"
            )

        quarter = _pick(point, "quarter", "period")
        try:
            period = int(quarter) if quarter is not None else None
        except (TypeError, ValueError):
            period = None

        shots.append(
            Shot(
                game_id=game_id,
                player_id=str(lic),
                team_id=home_slug if is_home else away_slug,
                loc_x=x_mm,
                loc_y=y_mm,
                made=made,
                shot_type=shot_type,
                period=period,
            )
        )
    if skipped_out_of_court:
        logger.warning(
            "game_id=%s: %d tiro(s) descartado(s) por coordenadas fuera de pista "
            "(|posY| > %d o posX fuera de 0-%d mm)",
            game_id, skipped_out_of_court, _MAX_ABS_Y_MM, _MAX_X_MM,
        )
    if skipped_non_fg:
        logger.debug("game_id=%s: %d eventos no-tiro-de-campo descartados", game_id, skipped_non_fg)
    return shots

def _parse_minutes(raw: object) -> float:
    """"MM:SS" -> minutos en float. Igual que en euroleague.py/nba.py:
    nunca lanza, si no reconoce el formato devuelve 0.0 con un aviso."""
    text = str(raw).strip()
    if not text or text in {"-", "nan", "None"}:
        return 0.0
    if ":" in text:
        try:
            minutes_str, seconds_str = text.split(":", 1)
            return int(minutes_str) + int(seconds_str) / 60.0
        except ValueError:
            logger.warning("No se pudo parsear el campo de minutos %r", raw)
            return 0.0
    try:
        return float(text)
    except ValueError:
        logger.warning("Formato de minutos no reconocido: %r; se registra como 0.0", raw)
        return 0.0
