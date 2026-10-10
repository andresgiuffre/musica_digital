# trainer/configuracion_ejecucion.py
"""
Constantes de la capa de métricas de ejecución (tiempo de aire, densidad rítmica,
saltos melódicos, cruce dinámica×registro) de trainer/metricas_ejecucion.py.

TODOS los valores numéricos de este archivo son PROVISORIOS -- puntos de partida
razonables, no una fuente normativa, a validar/ajustar por el usuario (músico).
Mismo criterio que ya usa RANGOS_COMODOS en models.py.
"""

# Segundos de silencio que cortan un tramo de ejecución continua para un
# instrumento normal.
PAUSA_MINIMA_RESPIRO = 1.0

# Para oboe/corno inglés, una vez que el tramo en curso ya superó su propio
# umbral de "aviso", el corte exige una pausa de esta duración (el aire
# sobrante tarda más en exhalarse que en una respiración normal).
PAUSA_EXHALACION = 3.0

# aviso/critico en segundos de duración PONDERADA (ver mult_registro/mult_dinamica
# en metricas_ejecucion.detectar_tramos_de_aire). mult_grave se aplica cuando la
# nota está en el tercio grave del ámbito cómodo del instrumento (RANGOS_COMODOS);
# mult_agudo (solo trompeta, por pedido explícito) en el tercio agudo.
LIMITES_AIRE = {
    "flauta": {"aviso": 8, "critico": 14, "mult_grave": 0.8},      # incluye piccolo
    "flauta_alto": {"aviso": 6, "critico": 10, "mult_grave": 0.7},
    "flauta_bajo": {"aviso": 4, "critico": 7, "mult_grave": 0.5},
    "oboe": {"aviso": 20, "critico": 35, "mult_grave": 1.0},
    "corno_ingles": {"aviso": 18, "critico": 30, "mult_grave": 1.0},
    "clarinete": {"aviso": 12, "critico": 22, "mult_grave": 0.9},
    "clarinete_bajo": {"aviso": 9, "critico": 16, "mult_grave": 0.7},
    "fagot": {"aviso": 14, "critico": 24, "mult_grave": 0.6},
    "contrafagot": {"aviso": 7, "critico": 12, "mult_grave": 0.6},
    "saxo_soprano": {"aviso": 12, "critico": 20, "mult_grave": 0.8},
    "saxo_alto": {"aviso": 10, "critico": 17, "mult_grave": 0.8},
    "saxo_tenor": {"aviso": 9, "critico": 15, "mult_grave": 0.8},
    "saxo_baritono": {"aviso": 8, "critico": 14, "mult_grave": 0.8},
    "trompeta": {"aviso": 15, "critico": 25, "mult_grave": 1.0, "mult_agudo": 0.7},
    "trompa": {"aviso": 15, "critico": 25, "mult_grave": 0.9},
    "trombon": {"aviso": 12, "critico": 20, "mult_grave": 0.8},
    "trombon_bajo": {"aviso": 8, "critico": 14, "mult_grave": 0.6},
    "tuba": {"aviso": 6, "critico": 10, "mult_grave": 0.6},
    "voz": {"aviso": 8, "critico": 14, "mult_grave": 1.0},
}

# Instrumentos que SÍ matchean una entrada de RANGOS_COMODOS pero no respiran
# (cuerdas, piano, percusión, guitarra, arpa) deliberadamente no tienen entrada acá
# -- no es un olvido, metricas_ejecucion.py los reconoce igual mediante
# RANGOS_COMODOS/CANONICO_A_CLAVE_AIRE, solo que la métrica de aire no les aplica
# (sin alerta, sin aviso de "no reconocido": están correctamente identificados).

MULT_DINAMICA = {"ppp": 1.0, "pp": 1.0, "p": 1.0, "mp": 1.0, "mf": 1.0, "f": 0.8, "ff": 0.6, "fff": 0.5}

# Dinámica asumida antes de que aparezca la primera marca real en una parte, y
# para cualquier marca cuyo texto no esté en MULT_DINAMICA (sforzando, fp, rf,
# etc. -- no se ignoran en silencio, generan un aviso agrupado, ver
# metricas_ejecucion._resolver_dinamica_vigente).
DINAMICA_POR_DEFECTO = 'mf'

# Top-N por instrumento que devuelve cada métrica (no todas las ocurrencias).
TOP_N_TRAMOS_AIRE = 5
TOP_N_PICOS_DENSIDAD = 5
TOP_N_SALTOS = 5

# Ventana móvil de densidad rítmica, en SEGUNDOS reales (no compases) -- un mismo
# pasaje suena más denso a tempo rápido que a tempo lento, aunque la partitura se
# vea igual. Confirmado con el usuario.
VENTANA_DENSIDAD_SEGUNDOS = 4.0
PASO_VENTANA_SEGUNDOS = 1.0

# Un salto se cuenta como "mayor a una octava" si el intervalo es ESTRICTAMENTE
# mayor a esto (una octava exacta no cuenta).
UMBRAL_SALTO_SEMITONOS = 12

# Dos ataques consecutivos de una misma voz separados por un silencio REAL (de
# esa voz) de esta duración o más no se consideran un salto melódico -- son
# dos frases distintas, no un salto dentro de una misma línea (bug real
# reportado: un "salto" medido entre notas con ~11 compases de silencio en el
# medio). Provisorio/configurable, no calibrado contra un corpus real.
UMBRAL_SILENCIO_CORTA_SALTO_SEGUNDOS = 2.0

# --- FASE 2B: versión compacta de alertas_ejecucion para el prompt de Claude ---
# (ver metricas_ejecucion.compactar_alertas_para_prompt). A diferencia de
# TOP_N_PICOS_DENSIDAD (un tope de cantidad, siempre devuelve "los N picos más
# densos" aunque la obra entera sea tranquila), esto es un UMBRAL real: si
# ningún pico de un instrumento lo supera, ese instrumento no aporta nada al
# prompt -- evita mandarle a Claude "el momento más denso" de una obra donde
# nada es realmente notable. El panel UI sigue usando TOP_N_PICOS_DENSIDAD sin
# umbral, son consumidores distintos del mismo cálculo.
UMBRAL_DENSIDAD_NOTABLE = 3.0  # notas/segundo

# Topes de la versión compacta para el prompt -- acotan el costo en tokens sin
# importar qué tan grande sea la obra. MAX_ALERTAS_TOTAL_PROMPT es un tope
# GLOBAL (no solo por instrumento): una obra con muchos instrumentos no debe
# multiplicar el costo sin límite -- al recortar, se agrega una entrada
# {'tipo': 'alertas_omitidas', 'cantidad': N} para que ni el modelo ni quien
# lea el reporte final asuma que "no hay más" cuando en realidad se truncó.
MAX_ALERTAS_POR_INSTRUMENTO_PROMPT = 3
MAX_ALERTAS_TOTAL_PROMPT = 40

# Fracción del ámbito cómodo (RANGOS_COMODOS) que define el tercio grave/agudo
# para mult_registro (tiempo de aire) y para el cruce dinámica×registro. El corte
# grave es inclusive hacia abajo (ps <= corte), así que cualquier nota por debajo
# del mínimo cómodo documentado también cuenta como grave, no solo las que caen
# dentro del tercio.
FRACCION_TERCIO_EXTREMO = 1 / 3

# Para el cruce dinámica×registro: "dinámica activa" extrema. 'p' y 'mf'/'f' solos
# quedan afuera a propósito -- son el caso normal, no el extremo.
DINAMICAS_EXTREMAS_FUERTE = {'f', 'ff', 'fff'}
DINAMICAS_EXTREMAS_SUAVE = {'pp', 'ppp'}

# ---------------------------------------------------------------------------
# Resolución de instrumento, nivel 2 (alias por nombre normalizado) -- ver
# metricas_ejecucion._resolver_instrumento_normalizado. Nivel 1 (clase real de
# music21, part.getInstrument()) no necesita tabla, usa
# metricas_ejecucion.CLASE_A_CANONICO directamente.
#
# Cada entrada: (alias_normalizados..., clave_canonica_de_RANGOS_COMODOS). El
# orden de esta lista NO importa -- la resolución es por coincidencia más larga,
# no por la primera que matchea (a diferencia del SINONIMOS_INSTRUMENTOS viejo en
# models.py, que si depende del orden -- no se tocó, esto es aditivo y exclusivo
# de este módulo nuevo).
#
# Alias ya pasaron por la misma normalización que se les aplica a los nombres
# reales antes de comparar (ver _normalizar_nombre_parte): minúsculas, sin
# tildes, sin puntos.
# ---------------------------------------------------------------------------
ALIAS_INSTRUMENTO = [
    (('piccolo', 'flautin', 'ottavino', 'picc'), 'Flautín'),
    (('flauta alto', 'alto flute', 'flauto contralto'), 'Flauta Alto'),
    (('flauta baja', 'flauta bajo', 'bass flute', 'flauto basso'), 'Flauta Bajo'),
    (('flauta', 'flute', 'flauto', 'fl'), 'Flauta'),
    (('corno ingles', 'english horn', 'cor anglais', 'corno inglese', 'eh'), 'Corno Inglés'),
    (('oboe', 'ob'), 'Oboe'),
    (('clarinete bajo', 'clarinete contrabajo', 'bass clarinet', 'clarinetto basso', 'bcl'), 'Clarinete Bajo'),
    (('clarinete', 'clarinet', 'clarinetto', 'cl'), 'Clarinete'),
    (('contrafagot', 'controfagotto', 'contrabassoon', 'cbsn'), 'Contrafagot'),
    (('fagot', 'fagotto', 'bassoon', 'bsn'), 'Fagot'),
    (('saxo soprano', 'soprano saxophone', 'soprano sax', 'sassofono soprano'), 'Saxo Soprano'),
    (('saxo alto', 'alto saxophone', 'alto sax', 'sassofono contralto'), 'Saxo Alto'),
    (('saxo tenor', 'tenor saxophone', 'tenor sax', 'sassofono tenore'), 'Saxo Tenor'),
    (('saxo baritono', 'baritone saxophone', 'baritone sax', 'sassofono baritono'), 'Saxo Barítono'),
    (('trompa', 'corno', 'french horn', 'horn', 'corno francese', 'hn'), 'Corno'),
    (('trompeta', 'tromba', 'trumpet', 'tpt'), 'Trompeta'),
    (('trombon bajo', 'bass trombone', 'trombone basso', 'btbn'), 'Trombón Bajo'),
    (('trombon tenor', 'tenor trombone', 'trombon', 'trombone', 'tbn'), 'Trombón'),
    (('tuba', 'tba'), 'Tuba'),
    (('violin', 'violino'), 'Violín'),
    (('viola',), 'Viola'),
    (('violoncello', 'violonchelo', 'cello', 'violoncelo'), 'Violonchelo'),
    (('contrabajo', 'double bass', 'contrabbasso', 'contrabass'), 'Contrabajo'),
    (('arpa', 'harp', 'arpa doppia'), 'Arpa'),
    (('piano', 'pianoforte'), 'Piano'),
    (('guitarra', 'guitar', 'chitarra'), 'Guitarra'),
    (('xilofono', 'xylophone'), 'Xilófono'),
    (('marimba',), 'Marimba'),
    (('vibrafono', 'vibraphone'), 'Vibráfono'),
    # Bug real corregido en esta revisión: "campanas tubulares"/"carillon"
    # estaban mal mapeados acá mismo a Glockenspiel -- son instrumentos
    # distintos, con rango distinto (ver RANGOS_COMODOS en models.py).
    # "Carillon" es justo el término francés/italiano habitual para campanas
    # tubulares DENTRO de una orquesta (no para un carillón de torre real).
    (('glockenspiel',), 'Glockenspiel'),
    (('campanas tubulares', 'tubular bells', 'chimes', 'carillon', 'campane tubolari'), 'Campanas Tubulares'),
    (('celesta', 'celeste'), 'Celesta'),
    (('timbal', 'timpani', 'timbales'), 'Timbal'),
    (('soprano', 'sop'), 'Soprano'),
    (('mezzosoprano', 'mezzo soprano'), 'Mezzosoprano'),
    (('contralto', 'alto voice', 'alto vocal', 'alt'), 'Contralto'),
    (('tenor', 'ten'), 'Tenor'),
    (('baritono', 'baritone', 'bar'), 'Barítono'),
    # 'Bajo' (voz) deliberadamente NO tiene alias genérico acá -- "bajo"/"bass"/
    # "basso" a secas son justo el término ambiguo (voz de bajo vs. contrabajo)
    # que models._resolver_instrumento_normalizado desempata por código
    # (presencia de note.lyrics), no por tabla. Ver TERMINOS_AMBIGUOS_BAJO.
]

# Términos ambiguos entre voz de Bajo y Contrabajo (cuerda) -- ninguno de los dos
# instrumentos aparece en ALIAS_INSTRUMENTO bajo estas palabras sueltas a
# propósito. Si el nombre normalizado de la parte es EXACTAMENTE uno de estos
# (no una subcadena de algo más largo, que ya matchearía 'Contrabajo' antes por
# ser más específico), se desempata viendo si la parte tiene letra.
TERMINOS_AMBIGUOS_BAJO = ('bass', 'bajo', 'basso')

# Percusión SIN altura definida -- reconocida (no dispara aviso de "instrumento
# no reconocido"), pero resuelve al sentinel models.INSTRUMENTO_SIN_ALTURA en
# vez de a una clave real de RANGOS_COMODOS (no hay "ámbito cómodo" que tenga
# sentido para algo sin altura). Entra en la misma competencia de coincidencia
# más larga que ALIAS_INSTRUMENTO (ver _resolver_instrumento_normalizado) --
# por eso es solo una tupla de keywords, no una lista de (keywords, canonico).
ALIAS_PERCUSION_SIN_ALTURA = (
    'caja', 'snare drum', 'snare', 'tamburo rullante',
    'bombo', 'bass drum', 'gran cassa',
    'platillos', 'platillo', 'cymbal', 'cymbals', 'piatti',
    'triangulo', 'triangle', 'triangolo',
    'pandereta', 'tambourine', 'tamburello',
    'wood block', 'woodblock', 'claves', 'cowbell', 'guiro',
    'tom tom', 'tom', 'bongo', 'bongos', 'conga', 'congas',
    'redoblante', 'tarola',
)

# Para desempatar "Alto" suelto (ver _resolver_instrumento_normalizado): si
# alguna de las OTRAS partes de la misma obra se normaliza a una de estas voces
# de coro, "Alto" se reconoce como Contralto (voz) -- un contexto típico de
# SATB es la señal más confiable además de letra propia.
VOCES_DE_CORO_PARA_DESEMPATE_ALTO = ('soprano', 'tenor', 'bajo', 'bass', 'basso', 'baritono', 'baritone')

# Términos que, si aparecen en el nombre normalizado, bloquean cualquier match
# de ALIAS_INSTRUMENTO para esa parte (aunque una subcadena suya matchearía algo
# -- ej. "baritone" solo matchea voz, pero "baritone horn"/"euphonium" NO es una
# voz ni tiene entrada en LIMITES_AIRE; se reporta como no reconocido en vez de
# adivinar mal). Ya normalizados (sin tildes, sin puntos, minúsculas).
TERMINOS_EXCLUIDOS = ('baritone horn', 'euphonium', 'bombardino', 'flicorno')

# "bass"/"contrabajo"/"basso" a secas (sin más contexto de instrumento/voz en el
# nombre) es ambiguo -- metricas_ejecucion.py lo desempata por código (presencia
# de note.lyrics en la parte: con letra, voz Bajo; sin letra, Contrabajo, no
# respira), no por tabla -- por eso ningún alias de este archivo incluye esos
# términos sueltos.

# Clave canónica de RANGOS_COMODOS -> clave de LIMITES_AIRE. Las que no están acá
# (cuerdas, piano, percusión, guitarra, arpa) son instrumentos reconocidos que
# correctamente no respiran -- no es un hueco de la tabla.
CANONICO_A_CLAVE_AIRE = {
    'Flautín': 'flauta',
    'Flauta': 'flauta',
    'Flauta Alto': 'flauta_alto',
    'Flauta Bajo': 'flauta_bajo',
    'Corno Inglés': 'corno_ingles',
    'Oboe': 'oboe',
    'Clarinete Bajo': 'clarinete_bajo',
    'Clarinete': 'clarinete',
    'Contrafagot': 'contrafagot',
    'Fagot': 'fagot',
    'Saxo Soprano': 'saxo_soprano',
    'Saxo Alto': 'saxo_alto',
    'Saxo Tenor': 'saxo_tenor',
    'Saxo Barítono': 'saxo_baritono',
    'Corno': 'trompa',
    'Trompeta': 'trompeta',
    'Trombón Bajo': 'trombon_bajo',
    'Trombón': 'trombon',
    'Tuba': 'tuba',
    'Soprano': 'voz',
    'Mezzosoprano': 'voz',
    'Contralto': 'voz',
    'Tenor': 'voz',
    'Barítono': 'voz',
    'Bajo': 'voz',
}
