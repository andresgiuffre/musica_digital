# trainer/metricas_ejecucion.py
"""
Capa de métricas de ejecución determinísticas (sin IA) del analizador de
orquestación -- mismo espíritu que calcular_estadisticas_parte/
evaluar_viabilidad_instrumental/detectar_duplicaciones_verificadas en views.py,
pero para todo lo que depende del eje temporal real: tiempo de aire continuo,
densidad rítmica local, saltos melódicos, y cruce entre dinámica y registro
extremo.

Se llama una vez por instrumento desde DENTRO del loop por-parte que ya existe
en _generar_analisis_orquestacion (views.py) -- no agrega una segunda pasada
sobre `parts` ni heartbeats nuevos, reutiliza los que ya hay ahí.

El resultado completo (`alertas_ejecucion`, con todos los niveles y sin topes
para el prompt) se mezcla a final_data recién después de la respuesta del
modelo, igual que alertas_viabilidad/densidad_por_compas -- nunca entra en
analysis_data/el prompt. FASE 2B agregó `compactar_alertas_para_prompt`
(abajo), que SÍ arma una versión reducida y acotada para que Claude pueda
citarla (`analysis_data['alertas_ejecucion_verificadas']`) -- ver el costo en
tokens medido en el plan de FASE 2B antes de tocar esto.
"""
import music21

from .configuracion_ejecucion import (
    PAUSA_MINIMA_RESPIRO, PAUSA_EXHALACION, LIMITES_AIRE, MULT_DINAMICA,
    DINAMICA_POR_DEFECTO, TOP_N_TRAMOS_AIRE, TOP_N_PICOS_DENSIDAD, TOP_N_SALTOS,
    VENTANA_DENSIDAD_SEGUNDOS, PASO_VENTANA_SEGUNDOS, UMBRAL_SALTO_SEMITONOS,
    UMBRAL_SILENCIO_CORTA_SALTO_SEGUNDOS,
    FRACCION_TERCIO_EXTREMO, DINAMICAS_EXTREMAS_FUERTE, DINAMICAS_EXTREMAS_SUAVE,
    CANONICO_A_CLAVE_AIRE, UMBRAL_DENSIDAD_NOTABLE, MAX_ALERTAS_POR_INSTRUMENTO_PROMPT,
    MAX_ALERTAS_TOTAL_PROMPT,
)
from .models import RANGOS_COMODOS, _resolver_instrumento_normalizado, INSTRUMENTO_SIN_ALTURA


# ---------------------------------------------------------------------------
# Resolución de instrumento, nivel 1: clase real de music21
# (part.getInstrument()) -- confiable SOLO cuando el archivo trae un objeto
# Instrument real (confirmado empíricamente contra music21 10.5.0: si la parte
# solo tiene un partName de texto, getInstrument() devuelve un Instrument
# genérico vacío, sin adivinar nada -- por eso hace falta el nivel 2,
# _resolver_instrumento_normalizado en models.py, como respaldo).
#
# Subclases más específicas DEBEN listarse antes que su genérica en
# CLASES_POR_ESPECIFICIDAD (fallback por isinstance, para subclases de music21
# no listadas explícitamente en el dict exacto) -- el dict exacto (type() ==)
# no depende de este orden.
# ---------------------------------------------------------------------------
CLASE_A_CANONICO = {
    music21.instrument.Piccolo: 'Flautín',
    music21.instrument.Flute: 'Flauta',
    music21.instrument.Oboe: 'Oboe',
    music21.instrument.EnglishHorn: 'Corno Inglés',
    music21.instrument.BassClarinet: 'Clarinete Bajo',
    music21.instrument.Clarinet: 'Clarinete',
    music21.instrument.Contrabassoon: 'Contrafagot',
    music21.instrument.Bassoon: 'Fagot',
    music21.instrument.SopranoSaxophone: 'Saxo Soprano',
    music21.instrument.AltoSaxophone: 'Saxo Alto',
    music21.instrument.TenorSaxophone: 'Saxo Tenor',
    music21.instrument.BaritoneSaxophone: 'Saxo Barítono',
    music21.instrument.Horn: 'Corno',
    music21.instrument.Trumpet: 'Trompeta',
    music21.instrument.BassTrombone: 'Trombón Bajo',
    music21.instrument.Trombone: 'Trombón',
    music21.instrument.Tuba: 'Tuba',
    music21.instrument.Violin: 'Violín',
    music21.instrument.Viola: 'Viola',
    music21.instrument.Violoncello: 'Violonchelo',
    music21.instrument.Contrabass: 'Contrabajo',
    music21.instrument.Harp: 'Arpa',
    music21.instrument.Piano: 'Piano',
    music21.instrument.Guitar: 'Guitarra',
    music21.instrument.Xylophone: 'Xilófono',
    music21.instrument.Marimba: 'Marimba',
    music21.instrument.Vibraphone: 'Vibráfono',
    music21.instrument.Glockenspiel: 'Glockenspiel',
    music21.instrument.TubularBells: 'Campanas Tubulares',
    music21.instrument.Celesta: 'Celesta',
    music21.instrument.Timpani: 'Timbal',
    music21.instrument.Soprano: 'Soprano',
    music21.instrument.MezzoSoprano: 'Mezzosoprano',
    music21.instrument.Alto: 'Contralto',
    music21.instrument.Tenor: 'Tenor',
    music21.instrument.Baritone: 'Barítono',
    music21.instrument.Bass: 'Bajo',
}

# Fallback por herencia (isinstance), para una subclase de music21 no listada
# arriba -- específico antes que genérico.
CLASES_POR_ESPECIFICIDAD = [
    (music21.instrument.BassClarinet, 'Clarinete Bajo'),
    (music21.instrument.Clarinet, 'Clarinete'),
    (music21.instrument.Contrabassoon, 'Contrafagot'),
    (music21.instrument.Bassoon, 'Fagot'),
    (music21.instrument.BassTrombone, 'Trombón Bajo'),
    (music21.instrument.Trombone, 'Trombón'),
] + list(CLASE_A_CANONICO.items())


# ---------------------------------------------------------------------------
# PUNTO 2 del reporte de bugs sobre "Flauta exagerada": espacio de alturas
# ÚNICO para TODO el analizador -- SONIDO REAL (concert pitch), nunca
# escrito. Confirmado (con el archivo real) que music21 preserva la altura
# ESCRITA al parsear un archivo -- nunca transpone a sonando por su cuenta --
# así que comparar el .ps crudo de una parte transpositora contra
# RANGOS_COMODOS (en escrito, ver su docstring en models.py) o contra otra
# parte (duplicaciones) sin convertir da resultados sin sentido. Dos casos
# reales que esto reproduce exactamente:
#   - La alerta real "Piccolo excede el registro grave... descendiendo a
#     Fa4": Fa4 ESCRITO suena Fa5 (el piccolo transpone +12), perfectamente
#     cómodo -- la alerta comparaba un escrito contra un rango que, para
#     colmo, estaba cargado en sonando (ver el fix de RANGOS_COMODOS arriba).
#   - Piccolo y Flauta con las MISMAS notas escritas en un pasaje: el código
#     lo clasificaba como "unísono" (intervalo escrito = 0), cuando en
#     realidad SUENA una octava (el piccolo sigue sonando +12 arriba).
#
# TRANSPOSICION_ESCRITO_A_SONANDO: semitonos a SUMAR a una altura ESCRITA
# para obtener la SONANDO, por instrumento canónico de RANGOS_COMODOS. Para
# los que tienen una clase real de music21 en CLASE_A_CANONICO, se deriva de
# instrument.transposition.semitones -- la fuente de verdad real, no un
# valor tipeado a mano (confirmado programáticamente contra las 35 clases
# antes de escribir esto). 4 instrumentos necesitan un override manual
# porque music21 NO modela su convención real (confirmado: transposition is
# None para los 4 en music21, aunque suenen distinto de lo escrito en la
# práctica real de orquestación):
#   - Contrafagot: music21 no le asigna transposición propia (Bassoon y
#     Contrabassoon comparten clase base sin .transposition), pero suena una
#     octava por debajo de lo escrito -- convención real, no opcional.
#   - Guitarra: se escribe una octava arriba de lo que suena -- convención
#     real de notación de guitarra (no es un instrumento "afinado en otra
#     tonalidad" en el sentido habitual, pero el efecto en este análisis es
#     el mismo: escrito ≠ sonando).
#   - Flauta Alto/Flauta Bajo: no existen como clases propias en music21 (no
#     hay AltoFlute/BassFlute) -- Flauta Alto en Sol transpone P4 justa
#     abajo, Flauta Bajo suena una octava (P8) abajo, por convención real.
# ---------------------------------------------------------------------------
def _construir_transposiciones_escrito_a_sonando():
    tabla = {}
    for clase, canon in CLASE_A_CANONICO.items():
        inst = clase()
        tabla[canon] = inst.transposition.semitones if inst.transposition is not None else 0
    tabla['Contrafagot'] = -12
    tabla['Guitarra'] = -12
    tabla['Flauta Alto'] = -5
    tabla['Flauta Bajo'] = -12
    return tabla


TRANSPOSICION_ESCRITO_A_SONANDO = _construir_transposiciones_escrito_a_sonando()

# Intervalo real (music21.interval.Interval) para los 4 overrides manuales --
# siempre cuarta justa u octava limpias, para preservar la grafía de la nota
# al transponer (ver pitch_a_sonando), igual criterio que
# _transportar_pitch_preservando_grafia en views.py.
_INTERVALOS_OVERRIDE_MANUAL = {
    'Contrafagot': 'P-8', 'Guitarra': 'P-8', 'Flauta Alto': 'P-4', 'Flauta Bajo': 'P-8',
}


def _intervalo_transposicion(canonico):
    """Intervalo real (music21.interval.Interval) de transposición escrito->
    sonando para este canónico, o None si no transpone o no se reconoce.
    Preferido sobre aritmética cruda de semitonos porque preserva la grafía
    real de la nota (ej. Do escrito de un clarinete en Sib transpone a Sib,
    no a un enarmónico elegido por music21 al reconstruir desde .ps)."""
    if TRANSPOSICION_ESCRITO_A_SONANDO.get(canonico, 0) == 0:
        return None
    for clase, canon in CLASE_A_CANONICO.items():
        if canon == canonico:
            inst = clase()
            if inst.transposition is not None:
                return inst.transposition
            break
    nombre_intervalo = _INTERVALOS_OVERRIDE_MANUAL.get(canonico)
    return music21.interval.Interval(nombre_intervalo) if nombre_intervalo else None


def pitch_a_sonando(pitch, canonico):
    """Transpone un Pitch ESCRITO a sonido real usando el intervalo real del
    instrumento (ver _intervalo_transposicion) -- para mostrar nombres de
    nota. Si el instrumento no transpone o no se reconoce, devuelve el mismo
    Pitch sin tocar (nunca copia innecesariamente)."""
    intervalo = _intervalo_transposicion(canonico)
    return pitch.transpose(intervalo) if intervalo is not None else pitch


def rango_comodo_sonando(canonico):
    """RANGOS_COMODOS[canonico] (en ESCRITO, ver su docstring en models.py)
    ya convertido a sonido real -- la única fuente de verdad para comparar
    contra alturas reales de una parte en todo el analizador (punto 2 del
    reporte de bugs: "espacio de alturas único, sonido real")."""
    lo, hi = RANGOS_COMODOS[canonico]
    intervalo = _intervalo_transposicion(canonico)
    if intervalo is None:
        return (lo, hi)
    return (
        music21.pitch.Pitch(lo).transpose(intervalo).nameWithOctave,
        music21.pitch.Pitch(hi).transpose(intervalo).nameWithOctave,
    )


def _eventos_a_sonando(eventos, canonico):
    """Transpone el campo 'pitch' de cada evento de tipo 'nota' a sonido real
    -- ver pitch_a_sonando. Los eventos 'silencio' no tienen pitch, se
    devuelven sin tocar. Si el instrumento no transpone o no se reconoce,
    devuelve la misma lista sin copiar nada (ni el intervalo se calcula)."""
    intervalo = _intervalo_transposicion(canonico)
    if intervalo is None:
        return eventos
    return [
        {**ev, 'pitch': ev['pitch'].transpose(intervalo)} if ev['tipo'] == 'nota' else ev
        for ev in eventos
    ]


def resolver_instrumento(part, part_name, nombres_hermanos=None):
    """
    Devuelve (clave_canonica_de_RANGOS_COMODOS | models.INSTRUMENTO_SIN_ALTURA | None, nivel)
    -- nivel 1 si se resolvió por clase real de music21, nivel 2 si fue por
    nombre normalizado, None si no se reconoció nada (dispara el aviso de
    "instrumento no reconocido" del lado del llamador).

    `nombres_hermanos`: nombres de las DEMÁS partes de la misma obra, para
    desempatar "Alto" suelto en el nivel 2 (ver
    models._resolver_instrumento_normalizado) -- no afecta el nivel 1.
    """
    inst = None
    try:
        inst = part.getInstrument()
    except Exception:
        inst = None

    if inst is not None:
        clase = type(inst)
        if clase in CLASE_A_CANONICO:
            return CLASE_A_CANONICO[clase], 1
        # Percusión sin altura (caja, bombo, platillos, etc.) -- reconocida por
        # clase real de music21 (music21.instrument.UnpitchedPercussion cubre
        # SnareDrum/BassDrum/Woodblock/Cymbals/etc.), sin necesitar que el
        # nombre matchee ALIAS_PERCUSION_SIN_ALTURA. Antes de este fix, un
        # nombre no reconocido acá disparaba "instrumento no reconocido" a
        # pesar de estar perfectamente identificado por clase, solo que sin
        # altura que medir.
        if isinstance(inst, music21.instrument.UnpitchedPercussion):
            return INSTRUMENTO_SIN_ALTURA, 1
        for clase_conocida, canonico in CLASES_POR_ESPECIFICIDAD:
            if isinstance(inst, clase_conocida):
                return canonico, 1

    nombre = part_name or (getattr(inst, 'instrumentName', None) if inst else None) or ''
    return _resolver_instrumento_normalizado(nombre, part, nombres_hermanos), 2


# ---------------------------------------------------------------------------
# Mapa de tiempo a nivel de OBRA (no por parte) -- bug real encontrado y
# confirmado empíricamente: part.flatten().secondsMap de una parte SIN su
# propia marca de tempo NO ve la marca de tempo de otra parte del mismo Score
# (confirmado con un caso de prueba: oboe sin MetronomeMark propio, flauta con
# 90bpm -- el secondsMap del oboe daba 2.0s para una redonda, el default de
# 120bpm, no 2.666s/90bpm). En una partitura real el tempo se escribe UNA sola
# vez (típicamente sobre el pentagrama superior), nunca replicado en cada
# parte -- por eso esto tiene que calcularse una sola vez sobre
# score.flatten().secondsMap (confirmado que SÍ propaga el tempo
# correctamente a todas las partes) y pasarse a cada llamada de
# calcular_metricas_de_parte, nunca recalcularse por parte.
#
# tempo_asumido=True si no hay NINGÚN MetronomeMark con .number resuelto en
# toda la obra (dispara el aviso 'tempo_asumido', ver calcular_metricas_de_parte
# y el llamador en views.py). Límite conocido, verificado y no resuelto en esta
# fase: distinguir "no hay tempo en absoluto" de "hay un término sin número
# explícito, tipo 'Allegro', resuelto vía la tabla de music21" no dio una señal
# confiable tras un parseo real de MusicXML (el flag numberImplicit de
# MetronomeMark, que en memoria sí distingue ambos casos, se perdía al
# reparsear un archivo real en las pruebas) -- ambos casos se tratan igual acá
# (tempo_asumido=True, 120bpm), en vez de inventar una distinción que no se
# pudo verificar.
# ---------------------------------------------------------------------------
def construir_mapa_tiempos(score):
    tiempos_por_id = {id(e['element']): e for e in score.flatten().secondsMap}
    hay_tempo_resuelto = any(
        isinstance(el, music21.tempo.MetronomeMark) and el.number is not None
        for el in score.flatten().getElementsByClass(music21.tempo.TempoIndication)
    )
    return tiempos_por_id, (not hay_tempo_resuelto)


# ---------------------------------------------------------------------------
# Un único pase por parte: arma la lista de eventos (notas y silencios, cada
# uno etiquetado con su VOZ real -- ver más abajo -- y con tiempo real en
# segundos vía el mapa de tiempos de la OBRA precalculado, ver
# construir_mapa_tiempos) que consumen las 4 métricas de abajo -- evita
# recorrer la parte 4 veces. measureNumber se arrastra del `for m in
# measures` externo (mismo patrón que
# calcular_densidad_por_compas/_eventos_sonantes_por_compas en views.py),
# nunca se llama .measureNumber/.getContextByClass por nota.
#
# VOCES MÚLTIPLES (divisi): bug real confirmado con un archivo de producción
# -- `m.recurse()` NO recorre un compás con varias Voice en orden cronológico
# real, recorre la Voz 1 completa y RECIÉN DESPUÉS la Voz 2 completa. Esto
# rompía 3 cosas a la vez: (a) dinamica_vigente (una sola variable,
# actualizada en orden de RECORRIDO, no de tiempo real) podía "filtrarse" de
# una voz a la otra si sus offsets reales coincidían pero su orden de
# documento no; (b) un salto melódico fantasma entre la última nota de la Voz
# 1 y la primera de la Voz 2, que nunca suenan una a continuación de la otra;
# (c) tiempo de aire y densidad rítmica sumando/contando ambas voces como si
# fueran secuenciales en vez de simultáneas.
#
# El fix: por cada compás, se arman los candidatos de TODAS las voces (más
# los elementos que cuelgan directo del compás, fuera de cualquier Voice --
# típicamente una dinámica que aplica a todo el pentagrama, no a una sola
# voz) y se ordenan por su offset REAL en segundos (confirmado empíricamente
# que music21 sí incluye las Dynamic en secondsMap, con su offset real) --
# nunca por orden de documento/voz. dinamica_vigente sigue siendo UNA sola
# variable para toda la parte (no una por voz): una dinámica nueva, venga de
# la voz que venga, rige para todas las voces desde su momento real en
# adelante -- el caso típico en una partitura real es una marca de dinámica
# que vale para todo el atril, no una por voz. Lo que sí se corrige es que
# ahora se aplica en el momento temporal CORRECTO, no en el orden en que
# music21 recorre el documento.
#
# Cada evento lleva 'voz' (el id de su Voice, o None si el compás no tiene
# voces separadas o el elemento cuelga directo del compás) -- lo consumen
# detectar_saltos_melodicos/detectar_cruce_dinamica_registro para no mezclar
# voces distintas entre sí.
# ---------------------------------------------------------------------------
def _construir_eventos_parte(part, tiempos_por_id):
    eventos = []
    dinamica_vigente = DINAMICA_POR_DEFECTO
    dinamicas_no_reconocidas = set()
    hubo_dynamic = False

    _TIPOS_RELEVANTES = (
        music21.dynamics.Dynamic, music21.note.Rest, music21.note.Note, music21.chord.Chord,
    )

    for m in part.getElementsByClass(music21.stream.Measure):
        candidatos = []
        voces = list(m.voices)
        if voces:
            for v in voces:
                for el in v.recurse():
                    candidatos.append((v.id, el))
            # Elementos que cuelgan directo del compás, fuera de cualquier Voice
            # (ej. una dinámica que aplica a todo el pentagrama) -- no recursivo
            # a propósito, los de cada Voice ya se agregaron arriba.
            for el in m.getElementsByClass(_TIPOS_RELEVANTES):
                candidatos.append((None, el))
        else:
            for el in m.recurse():
                candidatos.append((None, el))

        # Orden CRONOLÓGICO real (offset en segundos), no de documento/voz --
        # ver el comentario grande arriba. Ante un empate exacto de offset,
        # una Dynamic se procesa antes que las notas de ese mismo instante,
        # para que la dinámica nueva ya rija desde la primera nota que
        # arranca justo ahí.
        def _clave_orden(item, _tiempos=tiempos_por_id):
            _voz, el = item
            entry = _tiempos.get(id(el))
            offset = entry['offsetSeconds'] if entry is not None else 0.0
            es_dinamica = 0 if isinstance(el, music21.dynamics.Dynamic) else 1
            return (offset, es_dinamica)

        candidatos.sort(key=_clave_orden)

        for voz_id, el in candidatos:
            if isinstance(el, music21.dynamics.Dynamic):
                hubo_dynamic = True
                if el.value in MULT_DINAMICA:
                    dinamica_vigente = el.value
                else:
                    dinamicas_no_reconocidas.add(el.value)
                continue

            entry = tiempos_por_id.get(id(el))
            if entry is None:
                continue

            if isinstance(el, music21.note.Rest):
                eventos.append({
                    'tipo': 'silencio', 'pitch': None,
                    'offset_segundos': entry['offsetSeconds'],
                    'duracion_segundos': entry['durationSeconds'],
                    'compas': m.number, 'dinamica': dinamica_vigente, 'voz': voz_id,
                })
            elif isinstance(el, music21.harmony.ChordSymbol):
                continue  # cifrado (hereda de Chord pero no suena -- ver tests.py)
            elif isinstance(el, music21.chord.Chord):
                if not el.pitches:
                    continue
                eventos.append({
                    'tipo': 'nota', 'pitch': max(el.pitches, key=lambda p: p.ps),
                    'offset_segundos': entry['offsetSeconds'],
                    'duracion_segundos': entry['durationSeconds'],
                    'compas': m.number, 'dinamica': dinamica_vigente, 'voz': voz_id,
                })
            elif isinstance(el, music21.note.Note):
                eventos.append({
                    'tipo': 'nota', 'pitch': el.pitch,
                    'offset_segundos': entry['offsetSeconds'],
                    'duracion_segundos': entry['durationSeconds'],
                    'compas': m.number, 'dinamica': dinamica_vigente, 'voz': voz_id,
                })
            # music21.note.Unpitched (percusión sin altura) y cualquier otro
            # elemento: ignorado, mismo criterio que _eventos_sonantes_por_compas.

    return eventos, dinamicas_no_reconocidas, hubo_dynamic


def _cortes_registro(ambito_comodo):
    """Límite grave/agudo (tercio inferior/superior, ver FRACCION_TERCIO_EXTREMO)
    del ámbito cómodo de RANGOS_COMODOS, en semitonos (.ps). El corte grave es
    inclusive hacia abajo -- una nota por debajo del mínimo cómodo documentado
    también cuenta como grave, no solo las que caen dentro del tercio."""
    comodo_min_ps = music21.pitch.Pitch(ambito_comodo[0]).ps
    comodo_max_ps = music21.pitch.Pitch(ambito_comodo[1]).ps
    ancho = comodo_max_ps - comodo_min_ps
    return comodo_min_ps + ancho * FRACCION_TERCIO_EXTREMO, comodo_max_ps - ancho * FRACCION_TERCIO_EXTREMO


def _fusionar_intervalos_con_demanda(notas_con_mult):
    """
    Dadas notas (de UNA o VARIAS voces, ya mezcladas) con (offset_segundos,
    duracion_segundos, mult = mult_registro*mult_din), devuelve
    (duracion_real, duracion_ponderada) de su UNIÓN en el tiempo -- sin doble
    conteo cuando dos o más se superponen (voces simultáneas).

    Para la duración ponderada, en cada micro-instante donde se superponen
    varias notas se usa el multiplicador MÁS EXIGENTE (el más chico, ya que
    ponderada = duración / mult) entre las que suenan en ese instante -- ej.
    si una voz toca grave en ff y la otra en registro cómodo a la vez, ese
    tramo pesa como si fuera la voz grave en ff, no la suma de ambas.
    """
    if not notas_con_mult:
        return 0.0, 0.0
    intervalos = [
        (n['offset_segundos'], n['offset_segundos'] + n['duracion_segundos'], n['mult'])
        for n in notas_con_mult
    ]
    puntos = sorted({p for ini, fin, _ in intervalos for p in (ini, fin)})
    duracion_real = 0.0
    duracion_ponderada = 0.0
    for a, b in zip(puntos, puntos[1:]):
        activos = [mult for (ini, fin, mult) in intervalos if ini <= a and fin >= b]
        if not activos:
            continue
        dur = b - a
        duracion_real += dur
        duracion_ponderada += dur / min(activos)
    return duracion_real, duracion_ponderada


def detectar_tramos_de_aire(eventos, clave_aire, ambito_comodo):
    """
    Tramos de ejecución continua (sin pausa suficiente) por instrumento, con
    duración real y ponderada (ver LIMITES_AIRE/MULT_DINAMICA en
    configuracion_ejecucion.py) contra los umbrales aviso/critico de ese
    instrumento. Un silencio corta el tramo si dura >= PAUSA_MINIMA_RESPIRO,
    salvo para oboe/corno_ingles una vez que el tramo ya superó su propio
    umbral de aviso, donde el corte exige >= PAUSA_EXHALACION (el aire
    sobrante tarda más en exhalarse).

    VOCES MÚLTIPLES: la duración (real y ponderada) se calcula sobre la
    UNIÓN de los intervalos de sonido de TODAS las voces -- nunca se suman
    como si fueran secuenciales (bug real confirmado: con 2 voces de igual
    ritmo, el tiempo salía exactamente el doble del real). El "silencio" que
    puede cortar un tramo también es el de la UNIÓN: un hueco solo cuenta
    como pausa si NINGUNA voz está sonando en ese momento.
    """
    limites = LIMITES_AIRE[clave_aire]
    es_oboe_o_corno_ingles = clave_aire in ('oboe', 'corno_ingles')
    mult_grave = limites.get('mult_grave', 1.0)
    mult_agudo = limites.get('mult_agudo', 1.0)  # solo trompeta lo define distinto de 1.0
    corte_grave, corte_agudo = _cortes_registro(ambito_comodo)

    notas = [ev for ev in eventos if ev['tipo'] == 'nota']
    if not notas:
        return []

    notas_con_mult = []
    for ev in notas:
        ps = ev['pitch'].ps
        mult_registro = 1.0
        if ps <= corte_grave:
            mult_registro = mult_grave
        elif clave_aire == 'trompeta' and ps >= corte_agudo:
            mult_registro = mult_agudo
        mult_din = MULT_DINAMICA.get(ev['dinamica'], 1.0)
        notas_con_mult.append({**ev, 'mult': mult_registro * mult_din})

    # Unión de los intervalos de sonido de TODAS las voces (sin importar cuál).
    intervalos = sorted(
        (n['offset_segundos'], n['offset_segundos'] + n['duracion_segundos']) for n in notas_con_mult
    )
    fusionados = [intervalos[0]]
    for ini, fin in intervalos[1:]:
        ult_ini, ult_fin = fusionados[-1]
        if ini <= ult_fin:
            fusionados[-1] = (ult_ini, max(ult_fin, fin))
        else:
            fusionados.append((ini, fin))

    # Cortar en tramos según los huecos REALES entre intervalos fusionados
    # (huecos de la unión -- una pausa solo cuenta si NINGUNA voz suena ahí).
    grupos = [[fusionados[0]]]
    ya_supero_aviso = False
    for ini, fin in fusionados[1:]:
        grupo_actual = grupos[-1]
        hueco = ini - grupo_actual[-1][1]
        notas_grupo_actual = [
            n for n in notas_con_mult
            if any(g_ini <= n['offset_segundos'] < g_fin for g_ini, g_fin in grupo_actual)
        ]
        _, ponderada_hasta_ahora = _fusionar_intervalos_con_demanda(notas_grupo_actual)
        ya_supero_aviso = ponderada_hasta_ahora >= limites['aviso']
        requiere = PAUSA_EXHALACION if (es_oboe_o_corno_ingles and ya_supero_aviso) else PAUSA_MINIMA_RESPIRO
        if hueco >= requiere:
            grupos.append([(ini, fin)])
        else:
            grupo_actual.append((ini, fin))

    tramos = []
    for grupo in grupos:
        ini_tramo, fin_tramo = grupo[0][0], grupo[-1][1]
        notas_tramo = [n for n in notas_con_mult if ini_tramo <= n['offset_segundos'] < fin_tramo]
        if not notas_tramo:
            continue
        duracion_real, duracion_ponderada = _fusionar_intervalos_con_demanda(notas_tramo)
        if duracion_ponderada >= limites['critico']:
            nivel = 'critico'
        elif duracion_ponderada >= limites['aviso']:
            nivel = 'aviso'
        else:
            nivel = 'ok'
        compases = [n['compas'] for n in notas_tramo]
        tramos.append({
            'compas_desde': min(compases),
            'compas_hasta': max(compases),
            'duracion_segundos': duracion_real,
            'duracion_ponderada': duracion_ponderada,
            'umbral_aviso': limites['aviso'],
            'umbral_critico': limites['critico'],
            'nivel': nivel,
        })

    tramos.sort(key=lambda t: t['duracion_ponderada'], reverse=True)
    return tramos[:TOP_N_TRAMOS_AIRE]


def calcular_densidad_ritmica(eventos):
    """
    MOMENTOS DE ATAQUE (no notas) por segundo en ventana móvil de
    VENTANA_DENSIDAD_SEGUNDOS, paso PASO_VENTANA_SEGUNDOS -- devuelve los picos
    más densos, uno por compás distinto (si varias ventanas consecutivas caen
    en el mismo compás de inicio, se cuenta una sola vez: el pico real, no un
    duplicado del mismo tramo). Ventana en SEGUNDOS reales, no compases --
    confirmado con el usuario, un pasaje suena más denso a tempo rápido que a
    tempo lento aunque la partitura se vea igual.

    VOCES MÚLTIPLES: un acorde o dos voces que atacan exactamente en el mismo
    instante cuentan como UN solo ataque, no uno por nota/voz -- lo que mide
    "densidad" acá es cuántos MOMENTOS de ataque distintos hay por segundo,
    no cuántas notas suenan en total (eso ya lo refleja, aparte, que cada
    ataque puede ser más o menos denso en notas simultáneas, pero no es lo
    que esta métrica mide). Esto de paso corrige el supuesto de offsets
    ORDENADOS que necesitan los dos punteros: antes, con varias voces, la
    lista de offsets no venía ordenada y el barrido dejaba de ser válido.

    Dos punteros en vez de recorrer todas las notas en cada ventana: O(ataques
    + ventanas) en vez de O(ataques × ventanas), importa en una obra larga.
    """
    ataques = [e for e in eventos if e['tipo'] == 'nota']
    if not ataques:
        return []

    compas_por_offset = {}
    fin_por_offset = {}
    for a in ataques:
        offset = a['offset_segundos']
        if offset not in compas_por_offset:
            compas_por_offset[offset] = a['compas']
        fin_por_offset[offset] = max(fin_por_offset.get(offset, 0.0), offset + a['duracion_segundos'])

    offsets = sorted(compas_por_offset)
    duracion_total = max(fin_por_offset.values())

    picos = []
    izq = der = 0
    n = len(offsets)
    t = 0.0
    while t < duracion_total:
        while izq < n and offsets[izq] < t:
            izq += 1
        ventana_fin = t + VENTANA_DENSIDAD_SEGUNDOS
        while der < n and offsets[der] < ventana_fin:
            der += 1
        cantidad = der - izq
        if cantidad > 0:
            picos.append({
                'notas_por_segundo': cantidad / VENTANA_DENSIDAD_SEGUNDOS,
                'compas': compas_por_offset[offsets[izq]],
            })
        t += PASO_VENTANA_SEGUNDOS

    picos.sort(key=lambda p: p['notas_por_segundo'], reverse=True)
    resultado = []
    compases_usados = set()
    for p in picos:
        if p['compas'] in compases_usados:
            continue
        resultado.append(p)
        compases_usados.add(p['compas'])
        if len(resultado) >= TOP_N_PICOS_DENSIDAD:
            break
    return resultado


def detectar_saltos_melodicos(eventos):
    """Intervalo (semitonos) entre ataques consecutivos DE LA MISMA VOZ
    (acordes -> nota más aguda, mismo criterio que _eventos_sonantes_por_compas
    en views.py). 'Mayor a una octava' es estrictamente > 12 semitonos -- una
    octava exacta no cuenta.

    VOCES MÚLTIPLES: nunca se mide un salto entre ataques de voces distintas
    -- cada voz se procesa por separado, ordenada por su propio offset real
    (bug real confirmado: con 2 voces, la lista plana sin ordenar generaba
    saltos fantasma entre la última nota de una voz y la primera de la otra).

    SILENCIO LARGO: tampoco se mide un salto entre dos ataques de la MISMA
    voz si entre ellos hay un hueco real >= UMBRAL_SILENCIO_CORTA_SALTO_SEGUNDOS
    -- son dos frases distintas, no un salto dentro de una misma línea
    (bug real reportado: un "salto" medido a través de ~11 compases de
    silencio).
    """
    por_voz = {}
    for ev in eventos:
        if ev['tipo'] != 'nota':
            continue
        por_voz.setdefault(ev['voz'], []).append(ev)

    saltos = []
    for ataques_voz in por_voz.values():
        ataques_ordenados = sorted(ataques_voz, key=lambda e: e['offset_segundos'])
        for anterior, actual in zip(ataques_ordenados, ataques_ordenados[1:]):
            hueco = actual['offset_segundos'] - (anterior['offset_segundos'] + anterior['duracion_segundos'])
            if hueco >= UMBRAL_SILENCIO_CORTA_SALTO_SEGUNDOS:
                continue
            saltos.append({
                'semitonos': abs(actual['pitch'].ps - anterior['pitch'].ps),
                'compas_desde': anterior['compas'],
                'compas_hasta': actual['compas'],
            })

    if not saltos:
        return {'maximo_semitonos': 0, 'cantidad_mayor_octava': 0, 'mas_grandes': []}

    return {
        'maximo_semitonos': max(s['semitonos'] for s in saltos),
        'cantidad_mayor_octava': sum(1 for s in saltos if s['semitonos'] > UMBRAL_SALTO_SEMITONOS),
        'mas_grandes': sorted(saltos, key=lambda s: s['semitonos'], reverse=True)[:TOP_N_SALTOS],
    }


def _detectar_cruce_dinamica_registro_una_voz(eventos_voz, corte_grave, corte_agudo):
    coincidencias = []
    for ev in sorted(eventos_voz, key=lambda e: e['offset_segundos']):
        ps = ev['pitch'].ps
        if ps <= corte_grave:
            registro = 'grave'
        elif ps >= corte_agudo:
            registro = 'agudo'
        else:
            continue
        dinamica = ev['dinamica']
        if dinamica not in DINAMICAS_EXTREMAS_FUERTE and dinamica not in DINAMICAS_EXTREMAS_SUAVE:
            continue
        coincidencias.append({
            'compas': ev['compas'], 'nota': ev['pitch'].nameWithOctave,
            'registro': registro, 'dinamica': dinamica,
        })

    resultado = []
    actual = None
    for c in coincidencias:
        if (actual and actual['registro'] == c['registro'] and actual['dinamica'] == c['dinamica']
                and c['compas'] - actual['compas_hasta'] <= 1):
            actual['compas_hasta'] = c['compas']
            actual['nota_hasta'] = c['nota']
        else:
            if actual:
                resultado.append(actual)
            actual = {
                'compas_desde': c['compas'], 'compas_hasta': c['compas'],
                'nota_desde': c['nota'], 'nota_hasta': c['nota'],
                'registro': c['registro'], 'dinamica': c['dinamica'],
            }
    if actual:
        resultado.append(actual)
    return resultado


def detectar_cruce_dinamica_registro(eventos, ambito_comodo):
    """Notas en zona extrema (tercio grave o agudo del ámbito cómodo) con
    dinámica vigente extrema (f/ff/fff o pp/ppp) en ese punto -- cualquiera de
    las 4 combinaciones registro×dinámica cuenta, no solo la más obvia.

    Agrupa en rangos de compases contiguos con el mismo (registro, dinámica)
    -- mismo criterio que detectar_duplicaciones_verificadas en views.py. Sin
    esto, un pasaje sostenido de 30 notas en ff daba 30 entradas casi
    idénticas (bug real encontrado al inspeccionar la salida JSON real antes
    de cerrar esta fase).

    VOCES MÚLTIPLES: la detección y el agrupamiento en rangos contiguos se
    hacen POR VOZ, por separado -- nunca se mezclan los compases de una voz
    con los de otra dentro de un mismo rango agrupado.
    """
    corte_grave, corte_agudo = _cortes_registro(ambito_comodo)
    por_voz = {}
    for ev in eventos:
        if ev['tipo'] != 'nota':
            continue
        por_voz.setdefault(ev['voz'], []).append(ev)

    resultado = []
    for eventos_voz in por_voz.values():
        resultado.extend(_detectar_cruce_dinamica_registro_una_voz(eventos_voz, corte_grave, corte_agudo))

    resultado.sort(key=lambda r: r['compas_desde'])
    return resultado


def _agregar_aviso(avisos_acumulados, tipo, instrumento, valor=None):
    """Agrupa por (tipo, instrumento, valor) con un contador -- nunca una
    entrada por ocurrencia/nota."""
    clave = (tipo, instrumento, valor)
    if clave not in avisos_acumulados:
        avisos_acumulados[clave] = {'tipo': tipo, 'instrumento': instrumento, 'valor': valor, 'ocurrencias': 0}
    avisos_acumulados[clave]['ocurrencias'] += 1


def calcular_metricas_de_parte(part, part_name, avisos_acumulados, tiempos_por_id, nombres_hermanos=None):
    """
    Punto de entrada por instrumento -- se llama desde DENTRO del loop
    por-parte que ya existe en _generar_analisis_orquestacion (views.py),
    justo al lado de evaluar_viabilidad_instrumental, reutilizando el
    heartbeat que ya se emite ahí (no hay una segunda pasada sobre `parts`).

    `tiempos_por_id` viene de construir_mapa_tiempos(score), calculado UNA
    sola vez para toda la obra antes del loop (ver el comentario grande ahí --
    nunca se recalcula por parte, un part.flatten().secondsMap propio no ve el
    tempo de las demás partes). `nombres_hermanos`: nombres de las DEMÁS
    partes de la obra, para desempatar "Alto" suelto (ver
    models._resolver_instrumento_normalizado).

    Devuelve (resultado_de_esta_parte, hubo_dynamic) -- el segundo valor se
    usa en el llamador para decidir el aviso de nivel-obra "este archivo no
    trae dinámica" (típico de MIDI).
    """
    eventos, dinamicas_no_reconocidas, hubo_dynamic = _construir_eventos_parte(part, tiempos_por_id)
    for valor in dinamicas_no_reconocidas:
        _agregar_aviso(avisos_acumulados, 'dinamica_no_reconocida', part_name, valor)

    canonico, _nivel_resolucion = resolver_instrumento(part, part_name, nombres_hermanos)

    # PUNTO 2 del reporte de bugs: espacio de alturas ÚNICO, sonido real --
    # ver el comentario grande junto a TRANSPOSICION_ESCRITO_A_SONANDO más
    # arriba. Se convierte ACÁ, una sola vez, antes de calcular cualquier
    # métrica -- las 4 funciones de abajo nunca vuelven a tocar .ps crudo
    # escrito. Si canonico es None o INSTRUMENTO_SIN_ALTURA, no hay
    # transposición conocida (_eventos_a_sonando no transforma nada).
    eventos = _eventos_a_sonando(eventos, canonico)

    resultado = {
        'tramos_aire': [],
        'densidad_ritmica': calcular_densidad_ritmica(eventos),
        'saltos_melodicos': detectar_saltos_melodicos(eventos),
        'cruce_dinamica_registro': [],
    }

    if canonico is None:
        _agregar_aviso(avisos_acumulados, 'instrumento_no_reconocido', part_name)
        return resultado, hubo_dynamic

    if canonico == INSTRUMENTO_SIN_ALTURA:
        # Percusión sin altura definida -- reconocida, pero ninguna métrica de
        # registro aplica (no hay "ámbito cómodo" que tenga sentido). Sin
        # aviso: está perfectamente identificada, solo que no respira ni tiene
        # registro. densidad_ritmica/saltos_melodicos ya salieron vacíos de
        # por sí (music21.note.Unpitched no genera eventos tipo 'nota', ver
        # _construir_eventos_parte).
        return resultado, hubo_dynamic

    ambito_comodo = rango_comodo_sonando(canonico)
    resultado['cruce_dinamica_registro'] = detectar_cruce_dinamica_registro(eventos, ambito_comodo)

    clave_aire = CANONICO_A_CLAVE_AIRE.get(canonico)
    if clave_aire is not None:
        resultado['tramos_aire'] = detectar_tramos_de_aire(eventos, clave_aire, ambito_comodo)
    # clave_aire None: instrumento reconocido (cuerdas/piano/percusión/
    # guitarra/arpa) que correctamente no respira -- sin alerta, sin aviso.

    return resultado, hubo_dynamic


# Avisos que cambian cómo se interpreta TODO lo demás (ej. "este instrumento
# no se reconoció" implica que ninguna ausencia de alerta sobre él significa
# "medido y sin hallazgo") -- nunca son lo primero que se recorta si hay que
# aplicar MAX_ALERTAS_TOTAL_PROMPT, a diferencia de una alerta de medición
# normal (esas sí se pueden recortar por severidad/magnitud).
_TIPOS_AVISO_PRIORITARIOS = {'instrumento_no_reconocido', 'dinamica_no_reconocida', 'tempo_asumido', 'obra_sin_dinamica'}


def _severidad_alerta_prompt(entrada):
    if entrada.get('tipo') in _TIPOS_AVISO_PRIORITARIOS:
        return (3, 0)
    nivel_rank = {'critico': 2, 'aviso': 1}.get(entrada.get('nivel'), 0)
    magnitud = entrada.get('duracion_ponderada_seg') or entrada.get('semitonos') or entrada.get('notas_por_segundo') or 0
    return (nivel_rank, magnitud)


def compactar_alertas_para_prompt(alertas_ejecucion):
    """
    Versión reducida y acotada de `alertas_ejecucion` (el dict completo, con
    todos los niveles y sin topes de costo, pensado para el panel UI) para
    mandarle a Claude dentro de analysis_data['alertas_ejecucion_verificadas']
    -- FASE 2B. Lista PLANA (no anidada por instrumento), para que el schema
    pueda citar cada entrada 1 a 1 (ver 'alertas_ejecucion_citadas' en
    ORQUESTACION_TOOL), mismo criterio que duplicaciones_verificadas.

    Filtros propios de este consumidor (no son los mismos topes/criterios que
    usa el panel UI -- son dos consumidores distintos del mismo cálculo):
    - tiempo_aire: nunca manda nivel 'ok', solo aviso/crítico.
    - salto_melodico: solo instrumentos con cantidad_mayor_octava > 0; manda
      el salto más grande nada más (no los TOP_N_SALTOS de 'mas_grandes',
      eso es solo para el panel).
    - cruce_dinamica_registro: tal cual (ya viene agrupado en rangos de
      compases contiguos desde FASE 2A).
    - densidad_ritmica: UMBRAL real (UMBRAL_DENSIDAD_NOTABLE), no un tope de
      cantidad -- si ningún pico de un instrumento lo supera, ese instrumento
      no aporta nada acá (a diferencia del panel UI, que siempre muestra
      "los N picos más densos" aunque la obra entera sea tranquila).
    - avisos: tal cual, ya vienen agrupados y son pocos.

    Topes de costo, acotan el prompt sin importar el tamaño de la obra:
    MAX_ALERTAS_POR_INSTRUMENTO_PROMPT por instrumento (tiempo_aire/
    densidad_ritmica), y MAX_ALERTAS_TOTAL_PROMPT como tope GLOBAL -- si se
    excede, se cortan las menos severas (los avisos epistémicos nunca se
    cortan primero, ver _TIPOS_AVISO_PRIORITARIOS) y se agrega una entrada
    {'tipo': 'alertas_omitidas', 'cantidad': N} para que no se asuma
    silenciosamente que no hay más.
    """
    compacto = []

    por_instrumento = {}
    for t in alertas_ejecucion.get('tramos_aire', []):
        if t['nivel'] == 'ok':
            continue
        por_instrumento.setdefault(t['instrumento'], []).append(t)
    for instrumento, tramos in por_instrumento.items():
        tramos_ordenados = sorted(tramos, key=lambda t: t['duracion_ponderada'], reverse=True)
        for t in tramos_ordenados[:MAX_ALERTAS_POR_INSTRUMENTO_PROMPT]:
            compacto.append({
                'tipo': 'tiempo_aire', 'instrumento': instrumento,
                'compas_desde': t['compas_desde'], 'compas_hasta': t['compas_hasta'],
                'duracion_ponderada_seg': round(t['duracion_ponderada'], 1),
                'umbral_seg': t['umbral_critico'] if t['nivel'] == 'critico' else t['umbral_aviso'],
                'nivel': t['nivel'],
            })

    for s in alertas_ejecucion.get('saltos_melodicos', []):
        if s['cantidad_mayor_octava'] > 0:
            mayor = max(s['mas_grandes'], key=lambda x: x['semitonos'])
            compacto.append({
                'tipo': 'salto_melodico', 'instrumento': s['instrumento'],
                'compas_desde': mayor['compas_desde'], 'compas_hasta': mayor['compas_hasta'],
                'semitonos': int(mayor['semitonos']), 'cantidad_mayor_octava': s['cantidad_mayor_octava'],
            })

    for c in alertas_ejecucion.get('cruce_dinamica_registro', []):
        compacto.append({
            'tipo': 'cruce_dinamica_registro', 'instrumento': c['instrumento'],
            'compas_desde': c['compas_desde'], 'compas_hasta': c['compas_hasta'],
            'registro': c['registro'], 'dinamica': c['dinamica'],
        })

    for d in alertas_ejecucion.get('densidad_ritmica', []):
        notables = sorted(
            (p for p in d['picos'] if p['notas_por_segundo'] >= UMBRAL_DENSIDAD_NOTABLE),
            key=lambda p: p['notas_por_segundo'], reverse=True,
        )
        for p in notables[:MAX_ALERTAS_POR_INSTRUMENTO_PROMPT]:
            compacto.append({
                'tipo': 'densidad_ritmica', 'instrumento': d['instrumento'],
                'compas': p['compas'], 'notas_por_segundo': round(p['notas_por_segundo'], 1),
            })

    compacto.extend(alertas_ejecucion.get('avisos', []))

    if len(compacto) > MAX_ALERTAS_TOTAL_PROMPT:
        compacto.sort(key=_severidad_alerta_prompt, reverse=True)
        omitidas = len(compacto) - MAX_ALERTAS_TOTAL_PROMPT
        compacto = compacto[:MAX_ALERTAS_TOTAL_PROMPT]
        compacto.append({'tipo': 'alertas_omitidas', 'cantidad': omitidas})

    return compacto
