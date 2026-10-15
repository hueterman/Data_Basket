"""Extractor de competiciones de la FEB (Federacion Espanola de
Baloncesto) via baloncestoenvivo.feb.es.

Implementa la interfaz comun definida en src/scraper/base.py
(BasketballSource). Cuarto modulo de fuente del proyecto.

Un objeto = una competicion concreta (p. ej. Primera FEB, LF Endesa,
Copa Espana...), identificada por los tres parametros reales que usa
el sitio -- confirmados con fetch, no documentados previamente en el
plano con este detalle:

    g={group_code}&t={year}&nm={slug}

    g=1  t=2026  nm=primerafeb   -> Primera FEB (masculina)
    g=4  t=2026  nm=lfendesa     -> Liga Femenina Endesa
    g=67 t=2026  nm=lfchallenge  -> LF Challenge (femenina, 2o nivel)
    g=9  t=2026  nm=lf2          -> Liga Femenina 2
    g=74 t=2026  nm=ligau        -> Liga U
    g=73 t=2026  nm=copaespaa    -> Copa Espana
    g=2  t=2026  nm=segundafeb   -> Segunda FEB (antigua LEB Oro)
    g=3  t=2026  nm=tercerafeb   -> Tercera FEB (antigua LEB Plata)

Las OCHO claves de arriba son las competiciones SOPORTADAS ahora mismo,
cada una con su propio `competition` real -- ver `_COMPETITIONS` y las
secciones "MULTIPLES COMPETICIONES", "AMPLIACION 2026-09-20 (b)" y
"AMPLIACION 2026-09-20 (c)" mas abajo para el listado completo de que
otras competiciones existen en el sitio (los campeonatos de España de
categorias de formacion, Minicopa LF Endesa...) y por que NO estan
implementadas (alguna por no haberse pedido todavia, otras por no
encajar en el modelo de "temporada con calendario" -- ver esas
secciones para el detalle de cada caso, incluido un riesgo real de
'temporada-fantasma' encontrado en Copa España/Liga U, y un cambio
estructural real en Tercera FEB entre temporadas, que limitan que años
historicos son fiables o comparables para cada una).

HISTORICO Y BOXSCORE RESUELTOS EN VIVO (2026-09-20) -- investigacion
completa a peticion del usuario ("Como ingerir datos de feb de años
anteriores"), verificando cada paso contra respuestas reales del
sitio (fetch simple, sin JS, exactamente lo que hace `requests` en
este modulo) antes de escribir ningun mapeo de campos:

- LA TEMPORADA SI ES SELECCIONABLE -- la conclusion anterior ("t= se
  ignora") era correcta solo para `resultados.aspx` en su forma de
  query-string clasica, pero el sitio tiene una URL alternativa,
  `calendario.aspx?g=&t=&nm=`, que SI respeta `t=` y ademas es mejor
  fuente para listar partidos: redirige a una ruta limpia
  (`/calendario/{slug}/{group_code}/{year}`) y devuelve TODA la
  temporada (las 34 jornadas de Liga Regular, 306 partidos en Primera
  FEB) en una sola peticion, con resultados reales para partidos ya
  jugados -- confirmado con varias temporadas reales (2010/11, 2022/23,
  2024/25) ademas de la 2026/27 en curso, con marcadores reales
  distintos de "*-*" (p. ej. "64-73") y el desplegable de temporadas
  del propio sitio mostrando datos hasta 1996/97. La pagina esta
  organizada como una cabecera `<h1 class="titulo-modulo">Jornada N
  DD/MM/AAAA</h1>` seguida de su `<table>` hermana (`nextElementSibling`)
  con una fila `<tr>` por partido: `td.equipo.local a[href*=Equipo.aspx]`
  (nombre + id real de equipo), `td.resultado a` (marcador "NN-NN" si
  ya se jugo) o `td.fecha a` (dia/fecha/hora si es futuro -- la propia
  CLASE del `<td>` central distingue jugado/pendiente, mas fiable que
  adivinar por el texto), `td.equipo.visitante a`. El grupo por
  defecto de `calendario.aspx` ya es "Liga Regular Único" en todos los
  años probados -- no hace falta seleccionar grupo a mano. LIMITACION
  real (no resuelta, documentada): esta vista NO trae fecha exacta por
  partido para partidos ya jugados, solo la fecha nominal de la
  jornada (la fecha exacta si esta disponible por partido individual
  en `Partido.aspx?p={id}`, ver mas abajo, pero pedirla ahi para cada
  partido solo para la fecha no compensa el coste de una peticion
  extra por partido). Tampoco se trae la fase de Play-Offs (solo Liga
  Regular) -- fuera de alcance de esta investigacion, no implementado.
  `list_teams()`/`list_games()` ahora SI usan el parametro `season`
  que reciben (si se pasa uno) en vez de ignorarlo con un aviso.
- BOXSCORE RESUELTO -- el selector antiguo (`table.tabla-datos`,
  `#divEstadisticas`/`.box-estadisticas`) NO EXISTE en el HTML real:
  `get_boxscore()` estaba efectivamente ROTO para cualquier partido,
  pasado o presente (siempre lanzaba FebSourceError por no encontrar
  ninguna tabla). Confirmado con un partido real ya jugado
  (game_id="2411822", Primera FEB 2024/25, HLA Alicante 64-73 UEMC
  Real Valladolid -- marcador identico al de calendario.aspx, dato
  cruzado, no solo un numero suelto): la ficha de partido
  (`Partido.aspx?p={id}`, redirige a `/partido/{id}`) tiene DOS
  paneles con los mismos datos -- `#DirectoPanel` (mejorado por JS,
  con la clase "js_on" activa) y `#EstaticoPanel` (oculto por CSS una
  vez el JS corre, pero PRESENTE EN EL HTML CRUDO que devuelve una
  peticion GET simple, confirmado con fetch sin ejecutar JS: el nombre
  "Estatico" es literal, es el contenido servido sin depender de
  JavaScript). Dentro de `#EstaticoPanel`: `.columna.equipo.local
  a[href*=Equipo.aspx]` / `.columna.equipo.visitante a[...]` dan el id
  y nombre reales de cada equipo (mismo id que en calendario.aspx);
  dos `<table>` sin clase ni id (tabla 0 = equipo local, tabla 1 =
  visitante, mismo orden que el marcador) con dos filas de cabecera de
  solo `<th>` (sin `<thead>`, por eso el selector antiguo basado en
  `thead th` tampoco habria funcionado) seguidas de las filas de
  jugador y la de totales, todas como `<tr>` HERMANOS directos de
  `<table>` -- SIN NINGUN `<tbody>` real en el HTML (ver aviso "SIN
  <tbody> REAL" mas abajo, correccion sobre lo que decia esta misma
  seccion antes) -- y clases CSS estables por columna -- lectura por
  clase, no por texto de cabecera ni por alias adivinados:

      td.inicial / td.dorsal / td.nombre.jugador (con <a href="Jugador.aspx?i={team_id}&c={player_code}">)
      td.minutos (MM:SS) / td.puntos / td.tiros.dos / td.tiros.tres / td.tiros.campo / td.tiros.libres
      td.rebotes.ofensivos / td.rebotes.defensivos / td.rebotes.total
      td.asistencias / td.recuperaciones / td.perdidas
      td.tapones.favor / td.tapones.contra / td.mates
      td.faltas.cometidas / td.faltas.recibidas / td.valoracion / td.balance

  Las columnas de tiro (tiros dos/tres/campo/libres) llevan el texto
  "M/A" seguido de un `<span class="porcentaje">` con el %, p. ej.
  "1/6 16,7%" -- se extrae solo "M/A" del principio del texto. La fila
  de totales de equipo esta marcada de forma explicita con
  `<tr class="row-total">`, no hay que adivinarla por texto ("total"/
  "totales"/"equipo") como hacia el codigo anterior. Con esto
  `TeamStatLine` ya se puede rellenar COMPLETA (t2/t3/tl hechos y
  intentados, rebotes of/def, tapones, perdidas...), algo que el
  codigo anterior dejaba a proposito incompleto por no tener las
  columnas reales.
- PLAYER_ID REAL DISPONIBLE -- a diferencia de lo que decia la version
  anterior de este docstring, el enlace de nombre de jugador (tanto en
  `Equipo.aspx?i={team_id}` como en el boxscore de `Partido.aspx`) SI
  lleva un identificador numerico real y ESTABLE entre ambos sitios:
  `Jugador.aspx?i={team_id}&c={CODIGO_PERSONA}` -- confirmado que el
  mismo `c=` aparece para el mismo jugador en la plantilla de su
  equipo y en el boxscore de un partido real. `list_players()` y
  `get_boxscore()` ahora usan ese `c=` como `player_id` (con el
  respaldo anterior -- `"{team_id}-{dorsal}"` o slug del nombre --
  solo si algun enlace no lo trajera). A diferencia de euroleague.py
  (donde el roster y el boxscore usan IDs de jugador que NO coinciden
  entre si, ver ese modulo), aqui SI se puede cruzar list_players()
  con get_boxscore() por ID sin depender del nombre.
- get_shots() no se ha podido re-verificar con contenido real todavia
  (sigue dependiendo del token + LiveStats.API "Directo", que segun
  el propio aviso mas abajo solo sirve partidos en juego o recien
  terminados) -- para un partido historico como el usado arriba es
  ESPERABLE que devuelva 404 (no un fallo del modulo), no se ha
  intentado arreglar aqui porque no hay ninguna fuente alternativa de
  tiros conocida para partidos ya cerrados en baloncestoenvivo.feb.es.

BUG REAL EN PRODUCCION, SIN <tbody> REAL (corregido 2026-09-20, tras
la primera ingesta real del usuario sobre 5 temporadas): la primera
version de `get_boxscore()` (arriba) seleccionaba filas con
`table.select("tbody tr")`, asumiendo -- por lo visto en el DOM del
navegador durante la investigacion en vivo -- que los `<th>`/`<tr>` de
cada tabla estaban dentro de un `<tbody>`. FALSO: el HTML CRUDO que
devuelve el servidor (lo que ve `requests`, sin JS) no tiene NINGUN
`<tbody>` en estas dos tablas -- los navegadores insertan uno
IMPLICITO al parsear `<table><tr>...` (comportamiento estandar de
HTML5), y por eso el DOM inspeccionado en el navegador durante la
investigacion original SI lo mostraba, enmascarando el problema.
BeautifulSoup con el parser `html.parser` (el que usa este modulo, el
mismo que ve `requests`) NO hace esa insercion implicita -- por eso
`table.select("tbody tr")` devolvia SIEMPRE una lista vacia, para
cualquier partido. Consecuencia real, confirmada re-descargando y
re-parseando con `html.parser` el HTML crudo de un partido real de la
ingesta (game_id="2486258", 2024/25): no solo fallaba la deteccion de
`tr.row-total` (el sintoma visible en el log de la ingesta real, un
aviso por cada equipo de cada partido) -- el bucle entero no procesaba
NINGUNA fila, así que `get_boxscore()` devolvia SIEMPRE listas vacias
de `team_stats` Y `player_stats`, sin lanzar excepcion, por lo que
`ingest_source()` contaba cada partido como "ok" con el boxscore
completamente vacio. Corregido seleccionando `table.select("tr")`
directamente (sin asumir ningun contenedor `<tbody>`/`<thead>`) --
las dos filas de cabecera (solo `<th>`, sin `<td>`) ya se descartaban
correctamente con el chequeo `row.find("td") is None`, que no depende
de cuantas haya ni de donde esten anidadas. `list_players()` tenia el
mismo supuesto pero SI sobrevivia por un respaldo ya existente
(`if not rows: ... table.select("tr")[1:]`) que por pura casualidad
producia resultados correctos (las filas de cabecera sin `<td>` se
descartaban de todas formas mas abajo por indice de columna fuera de
rango) -- se simplifico para seleccionar `tr` directamente desde el
principio, sin el paso intermedio confuso. Ver `decisiones_proyecto.md`
para el aviso completo de esta sesion.

MULTIPLES COMPETICIONES (añadido 2026-09-20, a peticion del usuario:
"el FEB solo ingerimos datos de competiciones masculinas o tambien
femeninas"): hasta ahora `FebSource` solo sabia de Primera FEB
(masculina, `g=1&nm=primerafeb`), fijo tanto en el codigo (`source`
como atributo de CLASE, `"feb"`) como en los scripts que la
construyen. Investigacion en vivo confirmada:

- LIGA FEMENINA ENDESA (`g=4&nm=lfendesa`) FUNCIONA IGUAL -- mismo
  `calendario.aspx` (respeta `t=`, redirige a
  `/calendario/lfendesa/4/{year}`, 30 jornadas en la 2025/26, marcador
  real confirmado tanto en la temporada en curso como en 2024/25 --
  partido real game_id="2412368", 47-87, cruzado contra el mismo
  marcador en `#EstaticoPanel` de `Partido.aspx`), mismo `Equipo.aspx`
  (mismas clases `th.nombre.jugador`/`td.dorsal`, plantilla real de 8
  jugadoras con `c=` real), mismo `#EstaticoPanel`/`Partido.aspx` para
  el boxscore (mismas clases CSS, misma fila `tr.row-total`, puntos de
  la tabla = marcador real). Ni un solo selector tuvo que cambiar --
  toda la logica de parseo de `list_teams()`/`list_players()`/
  `list_games()`/`get_boxscore()` es identica entre competiciones, solo
  cambian `group_code`/`slug` (que URL pedir), nunca la forma del HTML
  que responde.
- `nm=` (el slug) esta confirmado COSMETICO -- no afecta a que datos
  devuelve el servidor, solo a la URL limpia del redirect (probado
  pidiendo `g=2&nm=segundafeb` y `g=2&nm=leboro` para la misma
  temporada historica: mismo resultado exacto, 26 jornadas, misma
  primera jornada). Solo `g=` (group_code) importa de verdad.
- MULTIPLES COMPETICIONES REALES EN EL SITIO -- listado completo
  extraido en vivo del menu real de la home
  (`.site-menu .menu-item`, `span.menu-item-liga` = nombre,
  enlace de Calendario = group_code/slug), no adivinado:

      g=4  nm=lfendesa            LF Endesa (femenina, 1ª nivel)      -- AÑADIDA (competition="lfendesa")
      g=1  nm=primerafeb          Primera FEB (masculina, 1ª nivel)   -- YA SOPORTADA (competition="feb")
      g=67 nm=lfchallenge         LF Challenge (femenina, 2º nivel)
      g=2  nm=segundafeb          Segunda FEB (masculina, 2º nivel -- la "LEB Oro" de antes del cambio de nombre ~2021)
      g=9  nm=lf2                 Liga Femenina 2 (femenina, 3er nivel)
      g=3  nm=tercerafeb          Tercera FEB (masculina, 3er nivel -- la "LEB Plata" de antes del cambio de nombre)
      g=74 nm=ligau               Liga U (formativa)
      g=73 nm=copaespaa           Copa España
      g=19/20/25/26/40/41         Campeonatos de España por selecciones autonomicas (mini/infantil/cadete, mas./fem.)
      g=21/22/35/36/37/38/71/72   Campeonatos de España de Clubes (junior/cadete/infantil/mini, mas./fem.)
      g=55 nm=minicopalfendesa    Minicopa LF Endesa
      g=44 nm=fasefinal1divisinfemenin  Fase Final 1ª Division Femenina

  PREVISION SOBRE "CATEGORIAS DE LEB": el nombre "LEB" (Liga Española
  de Baloncesto) ya no aparece en el sitio -- las dos ligas que
  historicamente se llamaban asi (LEB Oro y LEB Plata) estan aqui como
  "Segunda FEB" (g=2) y "Tercera FEB" (g=3) desde el cambio de marca de
  la FEB (~2021). Son las candidatas directas si se quiere ampliar mas
  alla de Primera FEB/LF Endesa. Confirmado en vivo que Segunda FEB
  (g=2) tiene temporadas historicas normales (26 jornadas en 2020/21,
  2024/25...); Tercera FEB (g=3) devolvio solo 3 jornadas raras para
  2020/21 (posible temporada afectada por el COVID en categorias
  inferiores, no investigado a fondo) -- revisar antes de asumir que
  todas sus temporadas historicas son completas. NO se ha buscado "Liga
  EBA" (la liga regional de base por debajo de Tercera FEB) -- no
  aparece en el menu de la home, probablemente gestionada por las
  federaciones autonomicas y no centralizada aqui; no confirmado, no
  investigado a fondo.
- SOLO SE AÑADE `lfendesa` EN ESTA SESION -- el resto del listado de
  arriba queda documentado como PREVISION (candidatas confirmadas que
  existen y usan el mismo `calendario.aspx`/`Partido.aspx`/
  `Equipo.aspx`), no implementado: añadir cualquiera de ellas es tan
  sencillo como una entrada mas en `_COMPETITIONS` (mas su cableado en
  `ingest_data.py`/`ingest_historical.py`/`app/app.py`, ver esos
  ficheros), pero no se ha hecho todavia porque no se ha pedido.
- CAMBIO DE FIRMA: `FebSource.__init__` pasa de
  `(group_code, year, slug)` a `(competition, year)` -- `competition`
  es una clave de `_COMPETITIONS` (ahora mismo `"feb"` o `"lfendesa"`),
  que resuelve `group_code`/`slug` internamente Y fija `self.source =
  competition` (antes un atributo de CLASE fijo a `"feb"`) -- mismo
  patron ya usado en `EuroleagueSource` (euroleague/eurocup/nextgen,
  ver euroleague.py) para que cada competicion caiga en su propio
  `source` en la base de datos sin mezclarse. Todo lo demas (parseo,
  selectores, boxscore) es IDENTICO entre competiciones -- ninguna
  logica cambia, solo que URL pedir.

AMPLIACION 2026-09-20 (b) -- a peticion explicita del usuario ("implementa
el resto de categorias: LF Challenge, Liga Femenina 2, Liga U, Copa
España, y varios campeonatos de España por categorias de formacion"),
verificando en vivo cada competicion ANTES de añadirla (no se ha forzado
ninguna al mismo molde solo porque lo pidiera el usuario):

- AÑADIDAS 4 COMPETICIONES (misma forma que Primera FEB/LF Endesa,
  confirmado en vivo, `calendario.aspx` respeta `t=` de verdad y
  `#EstaticoPanel` tiene las mismas 2 tablas/columnas/`tr.row-total`):
      lfchallenge  g=67  LF Challenge (femenina, 2º nivel) -- liga
                          regular normal, 30 jornadas en 2026/27.
      lf2          g=9   Liga Femenina 2 -- liga regular normal, 26
                          jornadas en 2026/27 (formato variable en
                          temporadas pasadas, ver mas abajo).
      ligau        g=74  Liga U -- liga regular normal, 14 jornadas en
                          2026/27 (formato de temporada real, NO es una
                          serie de torneos sueltos como NextGen/ANGT --
                          confirmado con `calendario.aspx`).
      copaespaa    g=73  Copa España -- SI encaja en el mismo modelo de
                          jornadas (no es un cuadro de eliminatorias
                          suelto), pero cada edicion es un grupo pequeño
                          de solo 4 equipos a 3 jornadas (liguilla), no
                          una temporada larga -- ver aviso de riesgo mas
                          abajo, `t=` NO es fiable para todas sus
                          ediciones.
  Boxscore de un partido real jugado de cada una verificado contra
  `Partido.aspx` (`#EstaticoPanel`, mismas clases CSS, `tr.row-total`
  presente, mismo esquema de columnas que Primera FEB): lfchallenge
  game_id="2480105", lf2 game_id="2479683", ligau game_id="2513595",
  copaespaa game_id="2526163".
- RIESGO DE TEMPORADA-FANTASMA (hallazgo nuevo, confirmado en vivo,
  ver tambien el guardia añadido en `_list_games_raw()`): para ALGUNAS
  competiciones, pedir un `t=` para el que el sitio no tiene una
  edicion real no da pagina vacia ni error -- devuelve EN SILENCIO los
  partidos de la edicion ACTUAL, como si `t=` no se hubiera aplicado.
  Confirmado comparando el primer partido devuelto (equipos + game_id)
  entre varios años para cada competicion nueva:
      copaespaa (g=73): t=2024, t=2025 y t=2026 devuelven 3 ediciones
        REALES y distintas (equipos y game_id distintos cada vez) --
        pero t=2023 y anteriores (probado hasta t=2018) devuelven
        SIEMPRE la edicion de t=2026 (mismo game_id 2526163), sin
        avisar. Por eso HISTORICAL_SEASONS["copaespaa"] en
        ingest_historical.py SOLO trae ["2026","2025","2024"], nunca
        años anteriores.
      ligau (g=74): t=2019 a t=2024 devuelven TODOS la misma edicion de
        t=2026 (mismo game_id 2538911, misma jornada) -- ademas t=2025
        (la unica otra edicion "distinta") solo trae 1 partido suelto,
        no una temporada real. Conclusion: Liga U NO tiene historico
        fiable ninguno todavia -- por eso NO tiene entrada en
        HISTORICAL_SEASONS (ver ingest_historical.py), solo la temporada
        actual (2026, sin jugar todavia a fecha de esta sesion).
      lfchallenge (g=67) y lf2 (g=9): SI confirmadas con datos REALES y
        distintos entre varios años (lfchallenge probado en 2018/2021/
        2025; lf2 probado en 2019-2025, 7 temporadas seguidas) -- sin
        indicio del mismo fallo, se usan con confianza normal.
  El guardia en `_list_games_raw()` compara el año real de la fecha de
  cada partido devuelto contra el `year` pedido y lanza `FebSourceError`
  si la mayoria no encaja -- para que este fallo silencioso no vuelva a
  colar datos de la temporada equivocada sin que nadie se de cuenta
  (misma familia de bug que `<tbody>` y `resultados.aspx`, ver arriba).
- NO IMPLEMENTADOS, con motivo verificado en vivo (no se ha forzado
  nada al mismo molde solo por pedirlo el usuario):
    - Los 8 campeonatos de España de categorias de formacion pedidos
      (CE SSAA Mini/Cadete/Infantil masc./fem., g=19/20/40/41/25/26, y
      C ESP CLUBES Junior/Cadete/Infantil/Mini masc./fem.,
      g=21/22/35/36/37/38/71/72): confirmado en vivo que CADA UNO de
      estos 8 `group_code` devuelve SIEMPRE exactamente 1 jornada con 1
      solo partido (2 equipos), sea cual sea el `t=` pedido (probado
      con el `t=` que usa el propio menu de la home -- 2025 -- y con
      2026) -- es decir, esta vista solo tiene registrada la FINAL de
      cada campeonato (p. ej. "CE SSAA Cadete Masculino": Aragon 81-72
      Andalucia, un unico partido), no la fase de grupos/clasificatoria
      completa con decenas de equipos que tendria un campeonato
      nacional real. Esto NO encaja en el modelo de "competicion =
      temporada con calendario de partidos" que usa `FebSource`
      (`list_games()` devolveria siempre exactamente 1 partido,
      `list_teams()` exactamente 2 equipos) -- forzarlo habria sido
      adivinar que ese unico partido vale como "temporada", cuando en
      realidad el sitio simplemente no expone aqui el resto del
      campeonato (probablemente centralizado por las federaciones
      autonomicas, no en baloncestoenvivo.feb.es). Si en el futuro se
      confirma otro origen con la fase de grupos completa, se podria
      reconsiderar.
    - Segunda FEB / Tercera FEB (las antiguas LEB Oro/LEB Plata, g=2/g=3):
      siguen sin implementar -- no se han vuelto a pedir explicitamente
      en esta ronda (el mensaje del usuario listaba LF Challenge/LF2/
      Liga U/Copa España/formacion, no estas dos), se mantienen solo
      como candidatas documentadas en el listado de mas arriba.
    - Minicopa LF Endesa (g=55) y Fase Final 1ª Division Femenina
      (g=44): tampoco pedidas, tampoco investigadas en esta ronda.
- WIRING: las 4 competiciones nuevas se cablearon en
  `scripts/ingest_data.py` (temporada actual, "2026", opt-in igual que
  feb/lfendesa), `scripts/ingest_historical.py` (`HISTORICAL_SEASONS`
  con la salvedad de copaespaa/ligau de arriba) y `app/app.py`
  (`SIMPLE_SOURCES`/`HISTORICAL_SOURCES`/`HISTORICAL_DEFAULT_SEASONS`).

AMPLIACION 2026-09-20 (c) -- a peticion explicita del usuario ("Incluye
Segunda FEB, Tercera FEB"), verificando en vivo antes de añadirlas
(seguian solo documentadas como candidatas desde la seccion "MULTIPLES
COMPETICIONES" de arriba):

- SEGUNDA FEB (g=2, antigua LEB Oro) -- CONFIRMADA ROBUSTA: probada en
  vivo para t=2020 a t=2026 (7 temporadas seguidas), TODAS con 26
  jornadas, 182 partidos y 14 equipos reales y distintos entre si (sin
  ningun indicio del riesgo de "temporada-fantasma" de la seccion
  anterior) -- misma forma exacta que Primera FEB/Segunda... da igual
  el año, se comporta como una liga normal madura. Boxscore de un
  partido real verificado (game_id="2486849", 2025/26, marcador 78-66
  cruzado contra el total real de `#EstaticoPanel`). Se añade con la
  misma confianza y el mismo historico de 5 temporadas que
  feb/lfendesa/lfchallenge/lf2.
- TERCERA FEB (g=3, antigua LEB Plata) -- CONFIRMADA, pero con un
  CAMBIO ESTRUCTURAL REAL entre temporadas (no un fallo de parseo ni
  temporada-fantasma, verificado con equipos y game_id distintos y
  reales en cada temporada probada): la temporada actual (t=2026,
  2026/27) es una liga nacional completa igual que Segunda FEB -- 14
  equipos, 26 jornadas, Oct 2026-Abr 2027 -- pero las temporadas
  ANTERIORES (t=2025 a t=2020, las 5 probadas) son mucho mas pequeñas:
  solo 1 a 3 jornadas con 2 a 4 equipos, todas con fecha de finales de
  mayo (p. ej. t=2025: 4 equipos, 3 jornadas, 28-30/05/2026; t=2023:
  solo 2 equipos, 1 partido, 12/05/2024) -- consistente con que este
  `group_code` solo centralizaba historicamente una FASE FINAL/promo-
  cion de ascenso de unos pocos equipos a final de temporada, no la
  temporada regular completa de Tercera FEB (que hasta 2026-27 debia
  gestionarse a nivel de federacion autonomica, fuera de este sitio,
  igual que se sospecha de "Liga EBA" mas arriba) -- la FEB parece
  haber centralizado Tercera FEB como liga nacional completa recien
  esta temporada 2026-27. Boxscore de un partido real de la fase-final
  pequeña tambien verificado (game_id="2513647", 2025, marcador 62-77
  cruzado). SE AÑADE DE TODAS FORMAS a `_COMPETITIONS` y a
  `HISTORICAL_SEASONS` (con esta nota) porque los datos SON reales y
  verificados, no un artefacto -- pero quien analice el historico de
  "tercerafeb" debe saber que las temporadas 2021-2025 NO son
  comparables en volumen a partir de 2026 (pocos partidos, no toda la
  categoria), para no confundirlo con un fallo de ingesta.
- El guardia de "temporada-fantasma" (ver `_list_games_raw()`) NO se
  dispara para ninguna de las dos -- confirmado con las fechas reales
  de cada temporada probada (todas caen dentro de [year, year+1]).

`list_players(team_id)` -- IMPLEMENTADO en una segunda ronda de
exploracion en vivo, con los SELECTORES confirmados contra el HTML
real de `Equipo.aspx?i={team_id}` (cabecera `<table>` con
`<th class="nombre jugador">` / `puesto` / `dorsal` / `fecha
nacimiento` / `nacionalidad` / `formacion` / `altura` / `peso`, mas
una primera `<th>` vacia de foto -- se lee por CLASE de cabecera, no
por posicion fija). El CONTENIDO de fila SI se confirmo el
2026-09-20 contra una plantilla historica real (team_id=950779, HLA
Alicante, 2024/25 -- 12 jugadores reales con enlace
`Jugador.aspx?i={team_id}&c={CODIGO_PERSONA}`) -- ese `c=` es el
mismo identificador numerico real que usa el boxscore de
`get_boxscore()` (ver aviso "PLAYER_ID REAL DISPONIBLE" mas arriba),
asi que `player_id` ahora prefiere ese codigo sobre el respaldo
sintetico anterior (`"{team_id}-{dorsal}"` o slug del nombre), que se
mantiene solo para el caso (no visto todavia) de que un enlace no
traiga `c=`. (En temporadas sin plantilla publicada -- como 2026-27 a
fecha de esta sesion -- la tabla sigue vacia y list_players() sigue
devolviendo lista vacia con aviso, no un fallo: es un estado real,
no un problema de parseo.)

`get_shots(game_id)` -- IMPLEMENTADO tras localizar el endpoint/XHR
real (no estaba en ShotChart.js como parecia al principio -- ese
archivo solo define la CLASE que consume el JSON, `shotChartObject`,
nunca hace el fetch; el fetch esta en un archivo relacionado,
`widget.launcher.js`, que la ficha de partido tambien carga y que
gestiona las 5 pestañas dinamicas -Directo/Rankings/Ficha/Grafico de
tiro/Estadisticas- de `Partido.aspx`). Confirmado en vivo,
inspeccionando `widget.launcher.js` directamente (no adivinado):

    GET https://intrafeb.feb.es/LiveStats.API/api/v1/ShotChart/{game_id}
    Authorization: Bearer {token}

`{token}` es un JWT (RS256) embebido en CADA ficha de partido
(`Partido.aspx?p={id}`) dentro de un input oculto,
`#contentToken input` -- NO es una credencial de usuario ni de
sesion: se sirve igual a cualquier visitante anonimo (confirmado
decodificando el JWT real: `client_id=baloncestoenvivoapp`,
`role=[OverView,TeamStats,ShotChart,Ranking,KeyFacts,BoxScore]`,
valido 24h desde que se genera la pagina). `_get_live_token()` lo
extrae con una peticion GET normal a la ficha del partido antes de
llamar al endpoint.

CONFIRMADO EN VIVO que la autenticacion se acepta: una llamada real
con el token devuelve 404 "Not Found" (no 401/403) para un partido
sin jugar -- es decir, el fallo es "no hay datos para este partido
ahora mismo" (LiveStats.API parece servir solo partidos en juego o
recien terminados), no un fallo de credenciales.

CONTENIDO real de la respuesta CONFIRMADO EN VIVO 2026-09-22 (antes
solo se conocia por leer `ShotChart.js`, nunca se habia observado un
payload real completo -- ver "BUGS REALES corregidos 2026-09-22" en
get_shots() mas abajo, encontrados precisamente al hacer esta
verificacion) contra game_id=2220021 (Palmer Alma Mediterrania Palma -
Melilla Sport Capital) via `fetch()` autenticado desde el navegador
integrado:

    json.SHOTCHART.TEAM[0/1] = {id, name, clubCode, logo, teamCode,
                                 PLAYER: [{id, no, name, <stats agregadas
                                 del partido: pts/reb/ast/fga/fgm/...>}, ...]}
    json.SHOTCHART.SHOTS[]   = [{team: "0"|"1", player: "<dorsal>",
                                 quarter, x, y, m, t}, ...]

`TEAM[i].id` es el mismo ID numerico real que `Equipo.aspx?i=` (el que
usan `games`/`team_stats`/`players`) -- confirmado identico al de la
pagina Partido.aspx del mismo partido. `TEAM[i].PLAYER[].id` es el
mismo codigo real que `Jugador.aspx?i={team_id}&c={CODIGO}` que ya usan
list_players()/get_boxscore(). `SHOTS[].player` NO es ese id -- es el
DORSAL del jugador (el campo "no" de PLAYER[], confirmado cruzando los
120 tiros reales del partido de prueba contra el roster de cada
equipo, 0 discrepancias), asi que hay que resolverlo contra
`TEAM[team_index].PLAYER[]` para recuperar el id real (ver
get_shots()). `m` se mapea a `Shot.made` (nombre de variable "made" en
el codigo fuente real del sitio, `shoot.m`); `t` se guarda tal cual en
`shot_type` porque su significado exacto no esta confirmado (el
codigo fuente no lo interpreta, solo lo reenvia) -- confirmado que NO
es el tipo de tiro real (ver "BUGS REALES corregidos 2026-09-20(e)").

Nota adicional (no explotada todavia, solo documentada): el mismo
`widget.launcher.js` revela que TAMBIEN existe un API "Directo" con
mas datos en vivo por partido bajo la misma base
(`https://intrafeb.feb.es/LiveStats.API/api/v1/{BoxScore,KeyFacts,
Ranking,TeamStats}/{game_id}`, mismo token) -- una fuente alternativa
al boxscore estatico por HTML que ya usa `get_boxscore()` en este
modulo, mas rica pero solo disponible mientras el partido esta en
juego o recien terminado. Se deja constancia por si conviene migrar
`get_boxscore()` a ella en el futuro, pero no se ha hecho: el
metodo actual (parseo de `table.tabla-datos`) ya funciona sobre HTML
estatico permanente, sin depender de una ventana de tiempo.

HISTORIA (SUPERADA el 2026-09-20, se deja como contexto): la primera
version de `_list_games_raw()` leia `resultados.aspx` (una jornada por
pagina, sin soporte real de temporada -- ver aviso mas arriba) y tuvo
un bug real corregido tras la primera prueba en vivo del usuario (0
partidos devueltos por asumir que ambos nombres de equipo estaban en
el mismo enlace que el resultado, cuando en realidad son tres enlaces
y celdas separadas por sufijo estable de ASP.NET,
`_equipoLocalHyperLink`/`_equipoVisitHyperLink`/`_resultadoHyperLink`).
Ese fix ya no aplica directamente porque `_list_games_raw()` ahora lee
`calendario.aspx` en su lugar (ver aviso "HISTORICO Y BOXSCORE
RESUELTOS EN VIVO" arriba) -- pero el hallazgo que motivo, que los
enlaces de equipo llevan el ID numerico real (`Equipo.aspx?i=`), sigue
igual de valido y se sigue usando.

IDs usados en este modulo: `team_id` es el ID numerico real de
`Equipo.aspx?i={ID}`; si no se puede extraer, se usa como respaldo un
slug derivado del nombre completo del equipo. `player_id` es el
codigo numerico real de `Jugador.aspx?i={team_id}&c={CODIGO}` (ver
aviso "PLAYER_ID REAL DISPONIBLE" arriba); si no se puede extraer, se
usa como respaldo `"{team_id}-{dorsal}"` o un slug del nombre.
`game_id` es el parametro numerico `p` de `Partido.aspx` (p. ej.
"2535727"), tal y como aparece en los enlaces reales de
`calendario.aspx`.
"""

from __future__ import annotations

import logging
import re
import unicodedata

import requests
from bs4 import BeautifulSoup

from src.scraper.base import (
    BoxscoreData,
    BoxscoreNotPublished,
    GameRef,
    Player,
    PlayerStatLine,
    Shot,
    Team,
    TeamStatLine,
)

logger = logging.getLogger(__name__)

# Competiciones reales soportadas -- group_code/slug confirmados en vivo
# 2026-09-20 leyendo el menu real de baloncestoenvivo.feb.es (elemento
# `.site-menu .menu-item`, que trae el group_code+slug en su enlace de
# Calendario y el nombre real de la competicion en `.menu-item-liga`) --
# ver docstring del modulo, "MULTIPLES COMPETICIONES", para el listado
# completo de lo que existe en el sitio y por que solo estas dos estan
# aqui de momento. `nm=` (el slug) esta confirmado que NO afecta a que
# datos devuelve el servidor (es cosmetico, solo para la URL limpia del
# redirect) -- solo `g=` (group_code) importa de verdad.
_COMPETITIONS: dict[str, dict[str, str]] = {
    "feb": {"group_code": "1", "slug": "primerafeb", "label": "Primera FEB (masculina)"},
    "lfendesa": {"group_code": "4", "slug": "lfendesa", "label": "Liga Femenina Endesa"},
    # Las 4 siguientes se añadieron el 2026-09-20 (ver docstring del
    # modulo, "AMPLIACION 2026-09-20 (b)") tras confirmar en vivo que
    # calendario.aspx/Partido.aspx se comportan igual para las cuatro --
    # mismo #EstaticoPanel, mismas columnas, mismo formato de jornada.
    "lfchallenge": {"group_code": "67", "slug": "lfchallenge", "label": "LF Challenge (femenina, 2o nivel)"},
    "lf2": {"group_code": "9", "slug": "lf2", "label": "Liga Femenina 2 (femenina, 3er nivel)"},
    "ligau": {"group_code": "74", "slug": "ligau", "label": "Liga U (formativa)"},
    "copaespaa": {"group_code": "73", "slug": "copaespaa", "label": "Copa España"},
    # Añadidas 2026-09-20(c), a peticion explicita del usuario ("Incluye
    # Segunda FEB, Tercera FEB") -- ver docstring del modulo, "AMPLIACION
    # 2026-09-20 (c)" para el detalle de la verificacion en vivo,
    # incluido un hallazgo real sobre Tercera FEB (su estructura cambia
    # de forma real entre temporadas, no es un fallo de parseo).
    "segundafeb": {"group_code": "2", "slug": "segundafeb", "label": "Segunda FEB (masculina, 2o nivel -- antigua LEB Oro)"},
    "tercerafeb": {"group_code": "3", "slug": "tercerafeb", "label": "Tercera FEB (masculina, 3er nivel -- antigua LEB Plata)"},
}

_BASE = "https://baloncestoenvivo.feb.es"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_DEFAULT_TIMEOUT = 30

# API interno real que alimenta la pestaña "Grafico de tiro" de
# Partido.aspx -- confirmado en vivo inspeccionando widget.launcher.js
# (ver docstring del modulo). NO es un endpoint publico documentado.
_SHOTCHART_API = "https://intrafeb.feb.es/LiveStats.API/api/v1/ShotChart"

# Clases CSS reales por columna del boxscore (#EstaticoPanel de
# Partido.aspx, ver docstring del modulo, "BOXSCORE RESUELTO" --
# confirmado en vivo 2026-09-20 contra un partido real, no adivinado).
# Cada valor es la clase (o combinacion de clases) del <td>/<th> de esa
# columna; se seleccionan por CSS, no por texto de cabecera.
_BOXSCORE_COLUMNS = {
    "minutes": "minutos",
    "points": "puntos",
    "t2": "tiros.dos",
    "t3": "tiros.tres",
    "tc": "tiros.campo",
    "tl": "tiros.libres",
    "reb_off": "rebotes.ofensivos",
    "reb_def": "rebotes.defensivos",
    "reb_tot": "rebotes.total",
    "assists": "asistencias",
    "steals": "recuperaciones",
    "turnovers": "perdidas",
    "blocks_for": "tapones.favor",
    "blocks_against": "tapones.contra",
    "fouls_committed": "faltas.cometidas",
    "val": "valoracion",
    "plus_minus": "balance",
}

# "M/A" al principio del texto de una columna de tiro (el resto del
# texto es "M/A <span class='porcentaje'>NN,N%</span>", por eso SIN
# ancla de fin -- ver docstring del modulo).
_MADE_ATT_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)")

# Jugador.aspx?i={team_id}&c={CODIGO} -- codigo de persona real,
# estable entre plantilla (Equipo.aspx) y boxscore (Partido.aspx), ver
# docstring del modulo, "PLAYER_ID REAL DISPONIBLE".
_PLAYER_CODE_RE = re.compile(r"[?&]c=(\d+)")

# Cabecera de jornada en calendario.aspx: "Jornada N DD/MM/AAAA".
_JORNADA_HEADER_RE = re.compile(r"Jornada\s+(\d+)\s+(\d{1,2}/\d{1,2}/\d{4})")


def period_from_quarter(quarter) -> int | None:
    """Periodo (1-4 cuartos, 5+ prorrogas) a partir del campo `quarter` del ShotChart API
    de FEB. Solo se aceptan enteros positivos; cualquier otra cosa (vacio, texto) da None
    en vez de adivinar. Ojo: el formato de las prorrogas no esta confirmado en vivo --
    si salen como texto quedaran en None (comprobar con SELECT period, COUNT(*) FROM shots)."""
    try:
        q = int(str(quarter).strip())
    except (TypeError, ValueError):
        return None
    return q if q > 0 else None


class FebSourceError(RuntimeError):
    """Fallo al extraer datos de baloncestoenvivo.feb.es, con contexto
    de que peticion concreta ha fallado y por que -- mismo estandar de
    codigo del proyecto que en los demas modulos de src/scraper/."""


class FebBoxscoreNotPublished(FebSourceError, BoxscoreNotPublished):
    """La ficha del partido existe pero la FEB no publica sus estadisticas
    (0 tablas). Ver BoxscoreNotPublished."""


def _slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "equipo"


def _to_int(raw: object, default: int = 0) -> int:
    try:
        text = str(raw).strip()
        if not text or text in {"-", "—", "nan"}:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _parse_minutes(raw: object) -> float:
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


# Equipo.aspx?i={ID} -- ID numerico real de equipo (ver docstring del modulo).
_TEAM_ID_RE = re.compile(r"[?&]i=(\d+)")

# Marcador real ("82-74") en calendario.aspx / resultados.aspx (el
# placeholder de partido no jugado varia: "*-*" en resultados.aspx,
# un texto de dia/fecha/hora en calendario.aspx -- este regex no
# matchea ninguno de los dos, que es justo el punto: si no matchea,
# no esta jugado).
_SCORE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")

# Fecha "25/09/2026" (DD/MM/AAAA), formato usado tanto en
# resultados.aspx como en la cabecera de jornada de calendario.aspx.
_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")

# Fecha DD/MM/AAAA suelta dentro del texto de td.fecha (celda de
# partido pendiente en calendario.aspx, que tambien lleva dia de la
# semana y hora -- p. ej. "SÁBADO 26/09/2026 19:00" tras normalizar
# separadores de <br>), a diferencia de _DATE_RE que exige que la
# fecha sea TODO el texto (caso de la cabecera de jornada).
_FECHA_CELL_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")

# Enlace a un partido: tanto la forma clasica de query-string
# (Partido.aspx?p={id}) como la ruta limpia (/partido/{id}) -- el
# sitio usa ambas formas de forma intercambiable segun la pagina (ver
# docstring del modulo, patron ya visto en resultados.aspx/
# calendario.aspx y Equipo.aspx/equipo). Se aceptan las dos en vez de
# asumir una sola, ya que no se aislo cual usa exactamente cada celda
# de calendario.aspx.
_GAME_ID_HREF_RE = re.compile(r"(?:[?&]p=|/partido/)(\d+)")


def _team_id_from_href(href: str) -> str | None:
    match = _TEAM_ID_RE.search(href)
    return match.group(1) if match else None


def _player_code_from_href(href: str) -> str | None:
    match = _PLAYER_CODE_RE.search(href)
    return match.group(1) if match else None


def _game_id_from_href(href: str) -> str | None:
    match = _GAME_ID_HREF_RE.search(href)
    return match.group(1) if match else None


def _parse_date(raw: str) -> str | None:
    match = _DATE_RE.match(raw)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


class FebSource:
    """Un objeto = una competicion real de baloncestoenvivo.feb.es
    (`competition`, una clave de `_COMPETITIONS`). Para trabajar con
    varias a la vez se crean varias instancias, cada una con su propio
    `self.source` -- mismo patron que EuroleagueSource en euroleague.py
    (euroleague/eurocup/nextgen), adoptado aqui el 2026-09-20 al añadir
    Liga Femenina Endesa: antes `source` era un atributo de CLASE fijo
    ("feb"), lo que habria mezclado masculina y femenina bajo el mismo
    `source` en la base de datos (esquema con clave `(source, id...)`,
    sin ninguna columna de competicion/categoria aparte -- ver docstring
    del modulo, "MULTIPLES COMPETICIONES"). `competition="feb"` se
    mantiene como la clave de la ya existente Primera FEB masculina, sin
    renombrarla, para no romper los datos ya ingeridos con ese `source`."""

    def __init__(self, competition: str, year: str):
        if competition not in _COMPETITIONS:
            raise ValueError(
                f"Competicion desconocida: {competition!r}. Validas: "
                f"{sorted(_COMPETITIONS)} (ver docstring del modulo, "
                f"'MULTIPLES COMPETICIONES', para como añadir otra)."
            )
        info = _COMPETITIONS[competition]
        self.source = competition
        self.group_code = info["group_code"]
        self.slug = info["slug"]
        self.year = year
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # -- infraestructura HTTP ---------------------------------------------

    def _get_soup(self, url: str, context: str) -> BeautifulSoup:
        try:
            response = self._session.get(url, timeout=_DEFAULT_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FebSourceError(f"Fallo pidiendo {context}: {exc}") from exc
        return BeautifulSoup(response.text, "html.parser")

    def _calendario_url(self, year: str) -> str:
        """calendario.aspx en vez de resultados.aspx -- SI respeta la
        temporada y trae la temporada COMPLETA (todas las jornadas de
        Liga Regular) en una sola peticion, ver docstring del modulo
        ("HISTORICO Y BOXSCORE RESUELTOS EN VIVO", 2026-09-20)."""
        return f"{_BASE}/calendario.aspx?g={self.group_code}&t={year}&nm={self.slug}"

    def _year_for(self, season: str) -> str:
        """`season` (parametro de la interfaz comun) tiene prioridad
        sobre `self.year` (fijado al construir la instancia) cuando se
        pasa uno -- asi list_teams()/list_games() funcionan tanto si
        quien llama fijo la temporada en el constructor (patron de
        ingest_data.py) como si la pasa en cada llamada (patron de
        ingest_historical.py)."""
        return season if season else self.year

    # -- equipos / jugadores ------------------------------------------------

    def list_teams(self, season: str) -> list[Team]:
        """Deriva el listado de equipos de los partidos que devuelva
        calendario.aspx para `season` (o `self.year` si no se pasa
        ninguna) -- ver docstring del modulo."""
        games = self._list_games_raw(self._year_for(season))
        teams: dict[str, str] = {}
        for game in games:
            teams[game["home_team_id"]] = game["home_name"]
            teams[game["away_team_id"]] = game["away_name"]
        return [Team(team_id=tid, name=name, source=self.source) for tid, name in teams.items()]

    def list_players(self, team_id: str) -> list[Player]:
        """Plantilla de `team_id` (ID numerico real de Equipo.aspx?i=,
        ver docstring del modulo). Los selectores de columna estan
        confirmados en vivo; el contenido de fila NO -- ver docstring
        del modulo para el detalle completo. Devuelve lista vacia
        (con aviso) si la plantilla todavia no tiene filas, en vez de
        lanzar -- es un estado real posible (temporada sin empezar),
        no necesariamente un fallo de parseo."""
        soup = self._get_soup(
            f"{_BASE}/Equipo.aspx?i={team_id}", context=f"plantilla de team_id={team_id!r}"
        )

        name_th = soup.select_one("th.nombre.jugador")
        if name_th is None:
            raise FebSourceError(
                f"No se encontro la cabecera 'th.nombre.jugador' en la "
                f"plantilla de team_id={team_id!r} -- puede que el "
                f"equipo no exista o que el marcado haya cambiado (ver "
                f"docstring del modulo)."
            )
        table = name_th.find_parent("table")
        if table is None:
            raise FebSourceError(
                f"La cabecera de plantilla de team_id={team_id!r} no "
                f"esta dentro de una <table> -- marcado inesperado."
            )

        header_classes = [th.get("class", []) for th in table.select("th")]

        def _col_index(*classes: str) -> int | None:
            for idx, cls_list in enumerate(header_classes):
                if all(c in cls_list for c in classes):
                    return idx
            return None

        name_idx = _col_index("nombre", "jugador")
        dorsal_idx = _col_index("dorsal")
        if name_idx is None:
            raise FebSourceError(
                f"No se pudo localizar el indice de columna de nombre "
                f"en la plantilla de team_id={team_id!r} -- cabeceras "
                f"reales: {header_classes!r}."
            )

        players: list[Player] = []
        # Sin <tbody> real en el HTML (ver docstring del modulo,
        # "SIN <tbody> REAL" -- confirmado 2026-09-20 tras un fallo real
        # en produccion): la fila de cabecera es un <tr> mas, con solo
        # <th> y ningun <td> -- se descarta mas abajo por no tener
        # celdas suficientes, no recortando la primera fila a ciegas.
        rows = table.select("tr")

        for row in rows:
            cells = row.find_all("td")
            if name_idx >= len(cells):
                continue
            name_cell = cells[name_idx]
            name = name_cell.get_text(strip=True)
            if not name:
                continue

            # Codigo real de persona (Jugador.aspx?i={team_id}&c={CODIGO})
            # -- confirmado en vivo 2026-09-20 (ver docstring del modulo,
            # "PLAYER_ID REAL DISPONIBLE"): el mismo codigo identifica al
            # jugador en el boxscore de get_boxscore(), asi que se
            # prefiere sobre el ID sintetico de abajo.
            link = name_cell.find("a")
            numeric_id = _player_code_from_href(link.get("href", "")) if link else None

            dorsal = (
                cells[dorsal_idx].get_text(strip=True)
                if dorsal_idx is not None and dorsal_idx < len(cells)
                else ""
            )
            if numeric_id:
                player_id = numeric_id
            elif dorsal:
                player_id = f"{team_id}-{dorsal}"
            else:
                player_id = f"{team_id}-{_slugify(name)}"

            players.append(Player(player_id=player_id, name=name, team_id=team_id, source=self.source))

        if not players:
            logger.warning(
                "Plantilla de team_id=%s sin filas de jugador -- puede "
                "ser que la plantilla 2026-27 todavia no este publicada "
                "(ver docstring del modulo), no necesariamente un fallo "
                "de parseo.",
                team_id,
            )
        return players

    # -- partidos -----------------------------------------------------------

    def _list_games_raw(self, year: str) -> list[dict]:
        """Lee calendario.aspx (temporada `year`) en una sola peticion
        -- estructura real confirmada en vivo 2026-09-20 (ver docstring
        del modulo, 'HISTORICO Y BOXSCORE RESUELTOS EN VIVO'): cada
        jornada es una cabecera `<h1 class="titulo-modulo">Jornada N
        DD/MM/AAAA</h1>` seguida de su `<table>` hermana
        (`find_next_sibling`), con una fila `<tr>` por partido.
        `td.equipo.local`/`td.equipo.visitante` dan equipo+id real;
        `td.resultado` (marcador "NN-NN", partido jugado) o `td.fecha`
        (dia/fecha/hora, pendiente) distinguen jugado/pendiente por la
        CLASE del `<td>`, no por el texto. Solo `td.fecha` trae fecha
        EXACTA por partido -- para partidos ya jugados se usa la fecha
        nominal de la jornada (limitacion documentada, ver docstring
        del modulo)."""
        soup = self._get_soup(
            self._calendario_url(year),
            context=f"calendario de g={self.group_code!r}, t={year!r}",
        )
        games: list[dict] = []
        try:
            for header in soup.select("h1.titulo-modulo"):
                jornada_match = _JORNADA_HEADER_RE.search(header.get_text(strip=True))
                round_number = int(jornada_match.group(1)) if jornada_match else None
                jornada_date = _parse_date(jornada_match.group(2)) if jornada_match else None

                table = header.find_next_sibling("table")
                if table is None:
                    logger.warning(
                        "Cabecera de jornada %r sin <table> hermana -- se "
                        "omite esa jornada (marcado inesperado).",
                        header.get_text(strip=True),
                    )
                    continue

                for row in table.select("tr"):
                    home_link = row.select_one("td.equipo.local a[href*='Equipo.aspx']")
                    away_link = row.select_one("td.equipo.visitante a[href*='Equipo.aspx']")
                    if home_link is None or away_link is None:
                        continue  # fila de cabecera de columnas u otra fila sin partido

                    home_name = home_link.get_text(strip=True)
                    away_name = away_link.get_text(strip=True)
                    home_team_id = _team_id_from_href(home_link.get("href", "")) or _slugify(home_name)
                    away_team_id = _team_id_from_href(away_link.get("href", "")) or _slugify(away_name)

                    result_cell = row.select_one("td.resultado")
                    fecha_cell = row.select_one("td.fecha")

                    home_score = away_score = None
                    played = False
                    game_date = jornada_date
                    game_link = None

                    if result_cell is not None:
                        game_link = result_cell.find("a")
                        score_match = _SCORE_RE.match(result_cell.get_text(strip=True))
                        if score_match:
                            home_score, away_score = int(score_match.group(1)), int(score_match.group(2))
                            played = True
                    if fecha_cell is not None:
                        game_link = fecha_cell.find("a") or game_link
                        # Partido pendiente: esta celda SI trae la fecha
                        # exacta del partido (a diferencia del jugado,
                        # que solo tiene la fecha nominal de la jornada
                        # -- ver docstring del modulo).
                        date_match = _FECHA_CELL_DATE_RE.search(
                            fecha_cell.get_text(separator=" ", strip=True)
                        )
                        if date_match:
                            game_date = _parse_date(date_match.group(0))

                    if game_link is None:
                        logger.warning(
                            "Fila de partido (jornada %r, %s vs %s) sin "
                            "enlace reconocible en td.resultado/td.fecha "
                            "-- se omite (marcado inesperado).",
                            round_number, home_name, away_name,
                        )
                        continue
                    game_id = _game_id_from_href(game_link.get("href", ""))
                    if game_id is None:
                        logger.warning(
                            "Enlace de partido (jornada %r, %s vs %s) sin "
                            "id reconocible: href=%r -- se omite.",
                            round_number, home_name, away_name, game_link.get("href", ""),
                        )
                        continue

                    games.append({
                        "game_id": game_id,
                        "round": round_number,
                        "home_name": home_name,
                        "away_name": away_name,
                        "home_team_id": home_team_id,
                        "away_team_id": away_team_id,
                        "date": game_date,
                        "home_score": home_score,
                        "away_score": away_score,
                        "played": played,
                    })
        except AttributeError as exc:
            raise FebSourceError(
                f"Forma inesperada en calendario.aspx (g={self.group_code!r}, t={year!r}): {exc}"
            ) from exc

        # Aviso REAL confirmado en vivo 2026-09-20 al ampliar a mas
        # competiciones (ver docstring del modulo, "RIESGO DE TEMPORADA-
        # FANTASMA"): para ALGUNAS competiciones (confirmado en Copa
        # España g=73 para t<2024 y en Liga U g=74 para t<2025),
        # calendario.aspx NO devuelve una pagina vacia ni un error cuando
        # se pide un `t=` sin edicion real -- devuelve en silencio los
        # partidos de la edicion ACTUAL, como si `t=` se hubiera
        # ignorado. Es la misma familia de fallo silencioso que ya paso
        # con resultados.aspx (ver seccion HISTORIA) y con el bug de
        # <tbody> -- una asuncion que parecia confirmada (calendario.aspx
        # SI respeta t=, cierto para feb/lfendesa/lfchallenge/lf2) que
        # resulto ser cierta solo A VECES. Para no ingerir en silencio
        # partidos bajo una temporada equivocada, se compara el año real
        # de la fecha de cada partido contra el `year` pedido -- si la
        # mayoria caen fuera de [year, year+1] (una liga puede arrancar
        # en `year` y cerrar en `year+1`), se lanza en vez de devolver
        # datos de la edicion equivocada.
        if games:
            try:
                expected_start = int(year)
            except (TypeError, ValueError):
                expected_start = None
            if expected_start is not None:
                with_date = [g for g in games if g["date"]]
                mismatched = [
                    g for g in with_date
                    if int(g["date"][:4]) not in (expected_start, expected_start + 1)
                ]
                if with_date and len(mismatched) > len(with_date) // 2:
                    raise FebSourceError(
                        f"calendario.aspx para g={self.group_code!r}, t={year!r} "
                        f"devolvio {len(mismatched)}/{len(with_date)} partidos con "
                        f"fecha fuera de [{expected_start}, {expected_start + 1}] -- "
                        f"muy probablemente la 'temporada-fantasma' confirmada en "
                        f"vivo para algunas competiciones (ver docstring del modulo, "
                        f"'RIESGO DE TEMPORADA-FANTASMA'): el sitio devuelve la "
                        f"edicion ACTUAL en vez de la pedida cuando `t={year!r}` no "
                        f"tiene datos propios para g={self.group_code!r}. No se "
                        f"devuelven estos partidos para evitar ingerirlos bajo la "
                        f"temporada equivocada -- revisa en vivo que temporadas son "
                        f"reales para esta competicion antes de forzar el paso."
                    )
        return games

    def list_games(self, season: str) -> list[GameRef]:
        """Enumera todos los partidos de Liga Regular de `season` (o
        `self.year` si no se pasa ninguna) pidiendo calendario.aspx UNA
        sola vez -- ver docstring del modulo. `round` sale de la
        cabecera de jornada; `date`, `home_score`/`away_score` y
        `played` salen de la fila real de cada partido (ver
        `_list_games_raw()`)."""
        year = self._year_for(season)
        raw_games = self._list_games_raw(year)
        if not raw_games:
            raise FebSourceError(
                f"No se encontro ningun partido en calendario.aspx para "
                f"g={self.group_code!r}, t={year!r} -- puede que la "
                f"temporada no exista o que el marcado haya cambiado "
                f"(ver docstring del modulo)."
            )
        return [
            GameRef(
                game_id=g["game_id"],
                source=self.source,
                season=year,
                round=g["round"],
                date=g["date"],
                home_team_id=g["home_team_id"],
                away_team_id=g["away_team_id"],
                home_score=g["home_score"],
                away_score=g["away_score"],
                played=g["played"],
            )
            for g in raw_games
        ]

    # -- boxscore / tiros -----------------------------------------------------

    def get_boxscore(self, game_id: str) -> BoxscoreData:
        """Ficha de partido real -- ver docstring del modulo, 'BOXSCORE
        RESUELTO' (confirmado en vivo 2026-09-20, no adivinado): el
        contenido completo vive en `#EstaticoPanel`, presente en el
        HTML crudo aunque CSS lo oculte tras cargar JS. Dos `<table>`
        sin clase/id (0=local, 1=visitante), columnas leidas por clase
        CSS estable (`_BOXSCORE_COLUMNS`), fila de totales marcada con
        `class="row-total"` -- sin heuristicas de texto."""
        soup = self._get_soup(
            f"{_BASE}/Partido.aspx?p={game_id}", context=f"acta de game_id={game_id!r}"
        )
        panel = soup.select_one("#EstaticoPanel")
        if panel is None:
            raise FebSourceError(
                f"No se encontro #EstaticoPanel en la ficha de "
                f"game_id={game_id!r} -- puede que el marcado del sitio "
                f"haya cambiado (ver docstring del modulo, 'BOXSCORE "
                f"RESUELTO')."
            )

        home_link = panel.select_one(".columna.equipo.local a[href*='Equipo.aspx']")
        away_link = panel.select_one(".columna.equipo.visitante a[href*='Equipo.aspx']")
        if home_link is None or away_link is None:
            raise FebSourceError(
                f"No se encontraron los enlaces de equipo local/visitante "
                f"(.columna.equipo.local/.visitante a[href*=Equipo.aspx]) "
                f"en la ficha de game_id={game_id!r} -- ver docstring del "
                f"modulo."
            )
        # Respaldo por slug si el enlace no trajera el id numerico real,
        # igual que en list_teams()/_list_games_raw() (ver docstring del
        # modulo, 'IDs usados en este modulo').
        home_team_id = _team_id_from_href(home_link.get("href", "")) or _slugify(home_link.get_text(strip=True))
        away_team_id = _team_id_from_href(away_link.get("href", "")) or _slugify(away_link.get_text(strip=True))

        tables = panel.find_all("table", recursive=False) or panel.find_all("table")
        if len(tables) == 0:
            raise FebBoxscoreNotPublished(
                f"La ficha de game_id={game_id!r} no trae tablas de estadisticas "
                f"(la FEB no las publica para este partido; ocurre p. ej. en "
                f"partidos de enero de 2021 de LEB Oro 2020/21)."
            )
        if len(tables) < 2:
            raise FebSourceError(
                f"Se esperaban 2 tablas de estadisticas (una por equipo) "
                f"en #EstaticoPanel de game_id={game_id!r}, se "
                f"encontraron {len(tables)} -- ver docstring del modulo."
            )

        team_stats: list[TeamStatLine] = []
        player_stats: list[PlayerStatLine] = []
        for team_id, table in zip((home_team_id, away_team_id), tables[:2]):
            found_totals = False

            def _col_text(row, key: str) -> str:
                cell = row.select_one(f"td.{_BOXSCORE_COLUMNS[key]}")
                return cell.get_text(separator=" ", strip=True) if cell else ""

            def _made_att(row, key: str) -> tuple[int, int]:
                match = _MADE_ATT_RE.match(_col_text(row, key))
                return (int(match.group(1)), int(match.group(2))) if match else (0, 0)

            try:
                for row in table.select("tr"):
                    if row.find("td") is None:
                        continue  # fila de cabecera (solo <th>, sin <tbody> real -- ver docstring)

                    is_totals = "row-total" in (row.get("class") or [])

                    t2_made, t2_att = _made_att(row, "t2")
                    t3_made, t3_att = _made_att(row, "t3")
                    ft_made, ft_att = _made_att(row, "tl")
                    reb_off = _to_int(_col_text(row, "reb_off"))
                    reb_def = _to_int(_col_text(row, "reb_def"))
                    ast = _to_int(_col_text(row, "assists"))
                    stl = _to_int(_col_text(row, "steals"))
                    turnovers = _to_int(_col_text(row, "turnovers"))
                    blocks_for = _to_int(_col_text(row, "blocks_for"))
                    fouls = _to_int(_col_text(row, "fouls_committed"))
                    val = _to_int(_col_text(row, "val"))

                    if is_totals:
                        found_totals = True
                        team_stats.append(
                            TeamStatLine(
                                game_id=game_id, team_id=team_id,
                                t2_made=t2_made, t2_att=t2_att,
                                t3_made=t3_made, t3_att=t3_att,
                                ft_made=ft_made, ft_att=ft_att,
                                reb_off=reb_off, reb_def=reb_def,
                                ast=ast, stl=stl,
                                turnovers=turnovers, blocks=blocks_for,
                                fouls=fouls, val=val,
                            )
                        )
                        continue

                    name_cell = row.select_one("td.nombre.jugador")
                    if name_cell is None:
                        continue
                    player_name = name_cell.get_text(strip=True)
                    if not player_name:
                        continue

                    minutes_text = _col_text(row, "minutes")
                    if not minutes_text or minutes_text in {"-", "0:00"}:
                        continue  # no jugo -- mismo criterio que euroleague.py/acb.py

                    player_link = name_cell.find("a")
                    player_code = _player_code_from_href(player_link.get("href", "")) if player_link else None
                    dorsal_cell = row.select_one("td.dorsal")
                    dorsal = dorsal_cell.get_text(strip=True) if dorsal_cell else ""
                    if player_code:
                        player_id = player_code
                    elif dorsal:
                        player_id = f"{team_id}-{dorsal}"
                    else:
                        player_id = f"{team_id}-{_slugify(player_name)}"

                    reb_tot_text = _col_text(row, "reb_tot")
                    rebounds = _to_int(reb_tot_text) if reb_tot_text else reb_off + reb_def
                    plus_minus_text = _col_text(row, "plus_minus")
                    plus_minus = _to_int(plus_minus_text, default=None) if plus_minus_text else None

                    player_stats.append(
                        PlayerStatLine(
                            game_id=game_id,
                            player_id=player_id,
                            player_name=player_name,
                            team_id=team_id,
                            minutes=_parse_minutes(minutes_text),
                            points=_to_int(_col_text(row, "points")),
                            rebounds=rebounds,
                            assists=ast,
                            val=val,
                            plus_minus=plus_minus,
                        )
                    )
            except AttributeError as exc:
                raise FebSourceError(
                    f"Forma inesperada en la tabla de estadisticas de "
                    f"game_id={game_id!r}, team_id={team_id!r}: {exc}"
                ) from exc

            if not found_totals:
                logger.warning(
                    "game_id=%s, team_id=%s: no se encontro fila "
                    "tr.row-total en la tabla de estadisticas -- no se "
                    "añade TeamStatLine para este equipo (ver docstring "
                    "del modulo).", game_id, team_id,
                )

        if not team_stats and not player_stats:
            # Ni un solo team_stats ni player_stats en NINGUNA de las dos
            # tablas -- casi seguro un fallo estructural de parseo (p. ej.
            # el bug real de produccion del 2026-09-20, "SIN <tbody>
            # REAL": el selector no encontraba ninguna fila y devolvia un
            # BoxscoreData vacio SIN avisar de que algo iba mal, que
            # ingest_source() cacheaba y contaba como "ok"). Se lanza en
            # vez de devolver un boxscore vacio en silencio, para que un
            # fallo asi no vuelva a pasar desapercibido -- ver docstring
            # del modulo.
            raise FebSourceError(
                f"get_boxscore(game_id={game_id!r}): no se extrajo NINGUN "
                f"dato (ni team_stats ni player_stats) de ninguna de las "
                f"dos tablas de #EstaticoPanel -- casi seguro un fallo de "
                f"parseo (marcado del sitio cambiado), no un boxscore "
                f"realmente vacio. Revisar los selectores contra un "
                f"partido real antes de asumir que el partido no tiene "
                f"datos."
            )

        return BoxscoreData(game_id=game_id, team_stats=team_stats, player_stats=player_stats)

    def _get_live_token(self, game_id: str) -> str:
        """Token Bearer real embebido en la ficha de partido (ver
        docstring del modulo) -- NO es una credencial de usuario, se
        sirve igual a cualquier visitante anonimo. Se pide con una
        peticion GET normal a Partido.aspx, la misma URL que usa
        get_boxscore()."""
        soup = self._get_soup(
            f"{_BASE}/Partido.aspx?p={game_id}",
            context=f"ficha de partido game_id={game_id!r} (token de autenticacion)",
        )
        input_tag = soup.select_one("#contentToken input")
        token = input_tag.get("value") if input_tag else None
        if not token:
            raise FebSourceError(
                f"No se encontro el token de autenticacion "
                f"(#contentToken input) en la ficha de game_id={game_id!r} "
                f"-- el marcado puede haber cambiado (ver docstring del "
                f"modulo)."
            )
        return token

    def get_shots(self, game_id: str) -> list[Shot]:
        """Coordenadas de tiro reales via el API interno "Directo" que
        usa la pestaña "Grafico de tiro" de la ficha de partido --
        endpoint y autenticacion confirmados en vivo (ver docstring del
        modulo).

        BUGS REALES corregidos 2026-09-20(e), encontrados al preparar la
        calibracion de coordenadas (ver SHOT_COORD_CALIBRATION en
        src/features.py) -- verificado en vivo contra game_id=2220021
        (Palmer Alma Mediterrania Palma - Melilla Sport Capital), esquema
        real de cada tiro: {"m": "0"|"1", "t": "MM:SS", "x": ..., "y":
        ..., "team": "0"|"1", "player": "...", "quarter": "..."}:

        1. `made`: el codigo anterior hacia `bool(raw_shot.get("m"))` --
           "m" es un STRING ("0" o "1"), y `bool("0")` en Python es
           `True` (cualquier string no vacio lo es) -- por eso TODOS los
           tiros guardados hasta ahora de las 7 competiciones FEB salian
           made=True sin excepcion (confirmado: 100% made=1 en las
           186986/147499/... filas ya guardadas en `shots`). Arreglado
           comparando contra el string "1" explicitamente. Verificado
           contra el boxscore real del partido de prueba: contando
           m=="1" por equipo (24 y 27) contra fgm real de las plantillas
           (24 y 27) y el total de tiros por equipo (64 y 56) contra fga
           real (64 y 56) -- coincidencia EXACTA en los dos equipos.
        2. `shot_type`: el codigo anterior guardaba `raw_shot.get("t")`
           pensando que era el tipo de tiro (analogo al "type" de
           NBA/Euroliga) -- en realidad "t" es el RELOJ del partido en
           el momento del tiro ("05:26", minutos:segundos), confirmado
           mirando la respuesta real (todas las 630k+ filas ya guardadas
           de FEB tienen un string con pinta de reloj, nunca "2"/"3").
           La API de FEB, a diferencia de NBA/Euroliga, NO expone un
           campo por tiro que diga si fue de 2 o de 3 (el boxscore SI
           trae p2m/p2a/p3m/p3a, pero agregados por jugador, no por tiro
           suelto) -- en vez de inventar un tipo derivado de la
           geometria (que duplicaria `zone` bajo otro nombre y lo
           haria pasar por dato real, violando "no adivinar"), se
           guarda el string "desconocido" explicitamente: honesto sobre
           que esta fuente no lo reporta, y sigue siendo una categoria
           valida para el modelo de tiro (train_shot.py) en vez de un
           NaN que lo descartaria del dataset de entrenamiento.

        Las filas YA guardadas en la tabla `shots` de estas 7
        competiciones (ingeridas con el codigo viejo, con este bug) NO
        se pueden arreglar con una migracion -- la cache de
        data/raw/.../shots/*.json guarda el Shot ya parseado (con el bug
        ya aplicado, ver docstring de raw_cache.py), no el JSON crudo
        original, asi que hace falta re-ingerir (golpear la API de
        verdad otra vez) para tener datos correctos. Ver
        train_shot.py (FEB_SHOTS_PENDING_REINGEST) -- estas fuentes se
        excluyen explicitamente del entrenamiento hasta que se
        reingieran."""
        token = self._get_live_token(game_id)
        url = f"{_SHOTCHART_API}/{game_id}"
        try:
            response = self._session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FebSourceError(
                f"Fallo pidiendo el grafico de tiro (ShotChart API) de "
                f"game_id={game_id!r}: {exc} -- confirmado en vivo que "
                f"este endpoint devuelve 404 (no 401/403) para un "
                f"partido que no esta/no ha estado en directo, por lo "
                f"que un fallo aqui probablemente significa que el "
                f"partido no tiene datos de LiveStats todavia, no un "
                f"problema de autenticacion (ver docstring del modulo)."
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise FebSourceError(
                f"Respuesta no-JSON del ShotChart API para "
                f"game_id={game_id!r}: {exc}"
            ) from exc

        shotchart = payload.get("SHOTCHART") if isinstance(payload, dict) else None
        if shotchart is None:
            raise FebSourceError(
                f"Respuesta del ShotChart API para game_id={game_id!r} "
                f"sin la clave 'SHOTCHART' esperada -- forma real: "
                f"{list(payload.keys()) if isinstance(payload, dict) else type(payload)!r} "
                f"(el esquema esperado esta sin confirmar contra un "
                f"ejemplo real, ver docstring del modulo)."
            )

        # BUGS REALES corregidos 2026-09-22, encontrados y verificados en
        # vivo (navegador integrado, fetch() autenticado contra el
        # ShotChart API real de game_id=2220021, 120/120 tiros
        # comprobados) al retomar el bug ya documentado de mas abajo
        # ("no adivinar" -- se corrigen los dos juntos porque el segundo
        # solo se vio AL verificar el primero contra un payload real
        # completo, nunca observado hasta ahora pese a que get_shots()
        # ya lleva desde 2026-09-20 en produccion):
        #
        # 1. `team_id`: el codigo anterior usaba team.get("name") (un
        #    STRING con el nombre completo del equipo) como si fuera el
        #    ID -- la respuesta real SI trae un `team["id"]` numerico
        #    (p. ej. "858102"), confirmado IDENTICO al ID real que
        #    devuelve Equipo.aspx?i= para ese mismo equipo (el mismo
        #    usado en `games`/`team_stats`/`players`) -- no habia que
        #    inventar nada, el dato ya estaba en el payload, solo no se
        #    leia. Esto rompia CUALQUIER union entre `shots` y el resto
        #    de tablas por team_id para las 7 competiciones FEB (~630k
        #    filas), nunca detectado porque ninguna feature existente lo
        #    necesitaba todavia (ver intento de opp_drtg_pre, sesion
        #    2026-09-22, que SI lo descubrio).
        # 2. `player_id`: el codigo anterior usaba raw_shot.get("player")
        #    directamente como ID -- ese campo es en realidad el DORSAL
        #    del jugador ("no" en PLAYER[], no su id real), confirmado
        #    cruzando los 120 tiros reales contra el roster de cada
        #    equipo (0 discrepancias) y viendo que el mismo dorsal ("22")
        #    aparece en AMBOS equipos con jugadores distintos. El id real
        #    SI esta en el payload (`TEAM[i].PLAYER[].id`, el mismo
        #    codigo de Jugador.aspx?i={team_id}&c={CODIGO} que ya usan
        #    list_players()/get_boxscore()) -- se resuelve el dorsal de
        #    cada tiro contra el roster de SU equipo (nunca global, el
        #    mismo dorsal se repite entre equipos) para recuperarlo. Esto
        #    corrompia el prior de jugador del modelo de tiro
        #    (`player_prior_fg`/`add_shot_priors()` en src/features.py,
        #    agrupa por (source, player_id)) para las 7 fuentes FEB --
        #    mezclaba el historial de jugadores DISTINTOS que compartieron
        #    dorsal en partidos distintos bajo un mismo "player_id"
        #    sintetico, y ademas hacia que pasar un player_id real a
        #    /predict_shot nunca encontrara coincidencia para estas 7
        #    fuentes (silenciosamente caia al prior de liga, sin error).
        #
        # Las filas YA guardadas en `shots` para estas 7 competiciones
        # tienen el mismo problema que el bug de made/shot_type de mas
        # abajo -- no se pueden arreglar con una migracion (la cache de
        # data/raw/ guarda el Shot ya parseado con el bug), hace falta
        # reingerir de verdad. Ver FEB_SHOTS_PENDING_REINGEST en
        # train_shot.py, vuelto a marcar con las 7 fuentes.
        raw_teams = shotchart.get("TEAM") or []
        team_ids: list[str] = []
        roster_by_dorsal: list[dict[str, str]] = []
        for idx, team in enumerate(raw_teams[:2]):
            team_id_real = team.get("id") if isinstance(team, dict) else None
            name = team.get("name") if isinstance(team, dict) else None
            # Mismo criterio de respaldo que el resto del modulo (ver
            # docstring, "IDs usados en este modulo"): slug del nombre
            # si el id real no viniera por algun motivo.
            team_ids.append(str(team_id_real) if team_id_real else (_slugify(name) if name else f"equipo-{idx}"))

            roster: dict[str, str] = {}
            raw_players = team.get("PLAYER") if isinstance(team, dict) else None
            for player in raw_players or []:
                if not isinstance(player, dict):
                    continue
                dorsal = player.get("no")
                player_id_real = player.get("id")
                if dorsal is not None and player_id_real:
                    roster[str(dorsal)] = str(player_id_real)
            roster_by_dorsal.append(roster)
        while len(team_ids) < 2:
            team_ids.append(f"equipo-{len(team_ids)}")
            roster_by_dorsal.append({})

        shots: list[Shot] = []
        for raw_shot in shotchart.get("SHOTS") or []:
            if not isinstance(raw_shot, dict):
                continue
            team_index = _to_int(raw_shot.get("team"), default=0)
            team_id = team_ids[team_index] if 0 <= team_index < len(team_ids) else str(team_index)
            roster = roster_by_dorsal[team_index] if 0 <= team_index < len(roster_by_dorsal) else {}
            dorsal = str(raw_shot.get("player", ""))
            # Respaldo si el dorsal no aparece en el roster de su equipo
            # (jugador dado de baja a mitad de temporada, error puntual
            # de la API...) -- mismo patron sintetico "{team_id}-{dorsal}"
            # que list_players()/get_boxscore() usan cuando falta el
            # codigo numerico real, nunca un player_id vacio silencioso.
            player_id = roster.get(dorsal) or f"{team_id}-{dorsal}"
            shots.append(
                Shot(
                    game_id=game_id,
                    player_id=player_id,
                    team_id=team_id,
                    loc_x=float(raw_shot.get("x") or 0),
                    loc_y=float(raw_shot.get("y") or 0),
                    made=(str(raw_shot.get("m")) == "1"),
                    shot_type="desconocido",
                    period=period_from_quarter(raw_shot.get("quarter")),
                )
            )
        return shots
