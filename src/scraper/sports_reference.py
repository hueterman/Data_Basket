"""Helper compartido para las fuentes de la red Sports Reference
(sports-reference.com/cbb y basketball-reference.com/wnba) -- ver
docstring de cbb.py y wnba.py para el contexto completo de cada una.

VERIFICADO EN VIVO (misma sesion que escribio cbb.py/wnba.py, con el
navegador -- no adivinado) que ambos sitios comparten exactamente el
mismo motor: HTML estatico con tablas identificadas por `id`, celdas
etiquetadas con `data-stat` (no por indice de columna), y la misma
tecnica de rendimiento -- varias tablas secundarias (avanzadas,
per-minute, per-poss, shooting, play-by-play, line-score,
four-factors) NO estan en el DOM que devuelve un fetch/requests.get()
normal, sino escondidas dentro de un comentario HTML `<!-- ... -->`
que el propio JavaScript del sitio "revela" al renderizar en un
navegador real -- confirmado comparando el DOM ya renderizado (todas
las tablas visibles) contra el HTML crudo de una peticion fetch()
normal (varias solo aparecen ahi dentro de un comentario). Las tablas
que de verdad necesita este proyecto ("roster" en ambos sitios,
"box-score-basic-{slug}" en CBB, "box-{TRI}-game-basic" en WNBA, y la
tabla de listado de escuelas/season-standings) NO estan comentadas --
se confirmo en vivo que llegan directas con una peticion GET simple.
`extract_table()` de abajo prueba primero el DOM normal (barato) y
solo si no aparece busca dentro de los comentarios, para que sirva
igual con las tablas comentadas si algun metodo futuro las necesita.

RITMO DE PETICIONES: Sports Reference es, con diferencia, la fuente
mas estricta del proyecto (ver plano, seccion 10, y
decisiones_proyecto.md) -- limite de facto de 20 peticiones/minuto
segun sus propias directrices, `Crawl-delay: 3` explicito en el
robots.txt de basketball-reference.com (confirmado en vivo), y un 429
real puede bloquear la IP/sesion hasta 24 horas -- no basta un
reintento. `SportsReferenceSession` fuerza un intervalo minimo entre
peticiones de forma automatica (nadie tiene que acordarse de dormir a
mano antes de cada llamada) y, ante un 429 real, lanza
`RateLimitedError` en vez de reintentar -- quien orqueste el scraping
debe PARAR el proceso por completo al verlo, no solo saltar al
siguiente partido/equipo (ver docstring de esa excepcion).

BLOQUEO REAL CONFIRMADO EN VIVO (usuario, 2026-09-08) en
sports-reference.com/cbb: 403 Forbidden desde la PRIMERA peticion con
`requests` normal, incluso ampliando las cabeceras HTTP a un juego
completo que replica una navegacion real de Chrome (Accept,
Accept-Encoding, Sec-Fetch-*, sec-ch-ua*...) -- el 403 persistio
identico tras ese cambio, lo que descarta que sea un problema de
cabeceras y confirma la sospecha de que Cloudflare esta filtrando por
la huella TLS/HTTP2 de la conexion (JA3/JA4), algo que la libreria
`requests` de Python (via urllib3/OpenSSL) no puede imitar por mucho
que se ajusten las cabeceras -- basketball-reference.com/wnba, misma
peticion, mismas cabeceras, SI dejo pasar `requests` sin problema, asi
que el bloqueo es especifico de la zona Cloudflare de
sports-reference.com, no un bloqueo generico a "requests" en toda la
red Sports Reference. SOLUCION APLICADA: `SportsReferenceSession` usa
`curl_cffi` (libreria de terceros que SI imita la huella TLS/HTTP2 de
un Chrome real, ademas de su API) en vez de `requests`, si esta
instalada -- ver `_HTTP_BACKEND`/`SportsReferenceSession.__init__`
mas abajo. Si `curl_cffi` no esta instalada, cae de vuelta a
`requests` (funcionara igual para basketball-reference.com/wnba, pero
lo mas probable es que siga dando 403 en sports-reference.com/cbb).
Instalacion: `pip install curl_cffi`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from bs4 import BeautifulSoup, Comment

try:
    from curl_cffi import requests as _curl_cffi_requests
    _HTTP_BACKEND = "curl_cffi"
except ImportError:
    _curl_cffi_requests = None
    _HTTP_BACKEND = "requests"

logger = logging.getLogger(__name__)

# > Crawl-delay: 3 de basketball-reference.com (confirmado en vivo) y por
# debajo del limite de facto de 20/min (3.0s) con margen de seguridad.
_MIN_INTERVAL_SECONDS = 3.6

# Version de Chrome que imita curl_cffi cuando esta instalada (ver
# docstring del modulo -- CONFIRMADO EN VIVO que ampliar solo las
# cabeceras HTTP de `requests` NO basto para sports-reference.com/cbb,
# el 403 persistio identico). Estas cabeceras "manuales" siguen aqui
# como PLAN B para cuando curl_cffi no esta instalada (backend
# "requests") -- con curl_cffi, el propio `impersonate=` ya genera un
# juego de cabeceras consistente con la huella TLS que imita, y
# sobreescribirlas a mano podria romper esa consistencia, asi que
# `SportsReferenceSession` solo aplica `_HEADERS` cuando el backend
# activo es "requests" (ver __init__ mas abajo).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

_DEFAULT_TIMEOUT = 30


class SportsReferenceError(RuntimeError):
    """Fallo generico al extraer datos de la red Sports Reference --
    CbbSourceError y WnbaSourceError heredan de esta (ver cbb.py/
    wnba.py), para que quien orqueste el scraping pueda capturar
    ambas fuentes con un solo except si quiere tratarlas igual (mismo
    motor, mismo rate limit, mismo helper por debajo)."""


class RateLimitedError(SportsReferenceError):
    """La fuente respondio 429 (Too Many Requests) -- ver aviso del
    modulo: esto puede significar un bloqueo de IP/sesion de hasta 24
    horas, no un simple pico de trafico. Quien orqueste el scraping
    DEBE PARAR el proceso por completo al ver este error (no solo el
    metodo o el partido/equipo que estaba pidiendo), y no debe
    reintentar automaticamente -- un reintento inmediato tras un 429
    real es exactamente el "spidering agresivo" que los terminos de
    uso de Sports Reference piden evitar explicitamente (ver
    decisiones_proyecto.md)."""


class SportsReferenceSession:
    """Sesion HTTP compartida por CbbSource/WnbaSource. Aplica el
    intervalo minimo entre peticiones documentado arriba de forma
    automatica -- cada llamada a `.get()` espera lo que haga falta
    antes de la peticion siguiente, contando desde la peticion
    anterior de ESTA MISMA sesion (si se crean varias sesiones no
    comparten el contador, asi que CbbSource/WnbaSource crean solo
    una por instancia, no una por metodo).

    Backend HTTP (ver aviso en el docstring del modulo -- CONFIRMADO
    EN VIVO que ampliar solo las cabeceras de `requests` NO basto para
    sports-reference.com/cbb, 403 identico antes y despues): si
    `curl_cffi` esta instalada (`pip install curl_cffi`), se usa esa
    libreria con `impersonate=` para imitar tambien la huella
    TLS/HTTP2 de un Chrome real, no solo sus cabeceras -- si no esta
    instalada, cae de vuelta a `requests` normal (con el juego de
    cabeceras manual de `_HEADERS`), que basta para
    basketball-reference.com/wnba pero probablemente NO para
    sports-reference.com/cbb (ver 403 confirmado)."""

    def __init__(
        self,
        min_interval_seconds: float = _MIN_INTERVAL_SECONDS,
        impersonate: str = "chrome124",
    ) -> None:
        self._backend = _HTTP_BACKEND
        if _curl_cffi_requests is not None:
            self._session: Any = _curl_cffi_requests.Session(impersonate=impersonate)
            # Con curl_cffi NO se sobreescriben las cabeceras a mano --
            # el propio `impersonate=` ya genera un juego consistente
            # con la huella TLS que imita (ver docstring de la clase);
            # forzar las de _HEADERS encima podria romper esa
            # consistencia y llamar mas la atencion, no menos.
        else:
            self._session = requests.Session()
            self._session.headers.update(_HEADERS)
        self._min_interval = min_interval_seconds
        self._last_request_at: float | None = None

    def get(self, url: str, *, context: str) -> Any:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            wait = self._min_interval - elapsed
            if wait > 0:
                time.sleep(wait)

        try:
            response = self._session.get(url, timeout=_DEFAULT_TIMEOUT)
        except Exception as exc:
            # Excepcion generica (no solo requests.RequestException):
            # curl_cffi tiene su propia jerarquia de errores de red, no
            # necesariamente subclases de las de `requests`.
            self._last_request_at = time.monotonic()
            raise SportsReferenceError(
                f"Fallo pidiendo {context} ({url}) [backend={self._backend}]: {exc}"
            ) from exc
        self._last_request_at = time.monotonic()

        status_code = getattr(response, "status_code", None)
        if status_code == 429:
            raise RateLimitedError(
                f"429 Too Many Requests pidiendo {context} ({url}) -- "
                f"posible bloqueo de IP/sesion de hasta 24h (ver "
                f"docstring de sports_reference.py). PARAR el proceso "
                f"por completo, no reintentar."
            )
        try:
            response.raise_for_status()
        except Exception as exc:
            if status_code == 403:
                raise SportsReferenceError(
                    f"403 Forbidden pidiendo {context} ({url}) [backend="
                    f"{self._backend}] -- CONFIRMADO EN VIVO (2026-09-08) que "
                    f"esto pasa en sports-reference.com/cbb con el backend "
                    f"'requests' (incluso con cabeceras HTTP completas), pero "
                    f"NO con basketball-reference.com/wnba -- ver docstring del "
                    f"modulo. Si esto ocurre TAMBIEN con backend='curl_cffi', "
                    f"la mitigacion actual (imitar la huella TLS de Chrome) no "
                    f"ha bastado y hace falta investigar mas (headers exactos "
                    f"de un Chrome real para ESTA pagina en concreto, u otro "
                    f"`impersonate=` mas reciente)."
                ) from exc
            raise SportsReferenceError(
                f"Fallo pidiendo {context} ({url}) [backend={self._backend}]: {exc}"
            ) from exc
        return response


def extract_table(html: str, table_id: str) -> BeautifulSoup | None:
    """Devuelve el `<table id="table_id">` pedido, este directo en el
    HTML o escondido dentro de un comentario (ver docstring del
    modulo). Prueba primero el DOM normal (mas barato); si no
    aparece ahi, escanea los nodos Comment del arbol y re-parsea el
    que contenga ese id. Devuelve None si no se encuentra en ninguno
    de los dos sitios -- nunca lanza: quien llama decide si eso es un
    error (tabla que deberia existir siempre) o un estado esperado
    (p. ej. una tabla que solo aparece para ciertos partidos)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=table_id)
    if table is not None:
        return table

    marker = f'id="{table_id}"'
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        if marker not in comment:
            continue
        inner_soup = BeautifulSoup(str(comment), "html.parser")
        table = inner_soup.find("table", id=table_id)
        if table is not None:
            return table
    return None


def to_int(raw: object, default: int | None = 0) -> int | None:
    """Conversion defensiva compartida por cbb.py/wnba.py: nunca
    lanza, si el texto no es un numero reconocible devuelve
    `default` (0 por convencion salvo que se pida None explicitamente,
    p. ej. para un plus_minus ausente)."""
    try:
        text = str(raw).strip()
        if not text or text in {"-", "—", "nan"}:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default
