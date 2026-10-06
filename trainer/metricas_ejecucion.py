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

Nunca entra en el prompt de Claude -- se mezcla a final_data recién después de
la respuesta del modelo, igual que alertas_viabilidad/densidad_por_compas (ver
el comentario en _generar_analisis_orquestacion). Impacto en tokens: cero.
"""
import music21

from .configuracion_ejecucion import (
    PAUSA_MINIMA_RESPIRO, PAUSA_EXHALACION, LIMITES_AIRE, MULT_DINAMICA,
    DINAMICA_POR_DEFECTO, TOP_N_TRAMOS_AIRE, TOP_N_PICOS_DENSIDAD, TOP_N_SALTOS,
    VENTANA_DENSIDAD_SEGUNDOS, PASO_VENTANA_SEGUNDOS, UMBRAL_SALTO_SEMITONOS,
    FRACCION_TERCIO_EXTREMO, DINAMICAS_EXTREMAS_FUERTE, DINAMICAS_EXTREMAS_SUAVE,
    CANONICO_A_CLAVE_AIRE,
)
from .models import RANGOS_COMODOS, _resolver_instrumento_normalizado


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


def resolver_instrumento(part, part_name):
    """
    Devuelve (clave_canonica_de_RANGOS_COMODOS | None, nivel) -- nivel 1 si se
    resolvió por clase real de music21, nivel 2 si fue por nombre normalizado,
    None si no se reconoció nada (dispara el aviso de "instrumento no
    reconocido" del lado del llamador).
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
        for clase_conocida, canonico in CLASES_POR_ESPECIFICIDAD:
            if isinstance(inst, clase_conocida):
                return canonico, 1

    nombre = part_name or (getattr(inst, 'instrumentName', None) if inst else None) or ''
    return _resolver_instrumento_normalizado(nombre, part), 2


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
# Un único pase por parte: arma la lista de eventos (notas y silencios, en
# orden, con tiempo real en segundos vía el mapa de tiempos de la OBRA
# precalculado -- ver construir_mapa_tiempos -- y la dinámica vigente en ese
# punto) que consumen las 4 métricas de abajo -- evita recorrer la parte 4
# veces. measureNumber se arrastra del `for m in measures` externo (mismo
# patrón que calcular_densidad_por_compas/_eventos_sonantes_por_compas en
# views.py), nunca se llama .measureNumber/.getContextByClass por nota.
# ---------------------------------------------------------------------------
def _construir_eventos_parte(part, tiempos_por_id):
    eventos = []
    dinamica_vigente = DINAMICA_POR_DEFECTO
    dinamicas_no_reconocidas = set()
    hubo_dynamic = False

    for m in part.getElementsByClass(music21.stream.Measure):
        for el in m.recurse():
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
                    'compas': m.number, 'dinamica': dinamica_vigente,
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
                    'compas': m.number, 'dinamica': dinamica_vigente,
                })
            elif isinstance(el, music21.note.Note):
                eventos.append({
                    'tipo': 'nota', 'pitch': el.pitch,
                    'offset_segundos': entry['offsetSeconds'],
                    'duracion_segundos': entry['durationSeconds'],
                    'compas': m.number, 'dinamica': dinamica_vigente,
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


def detectar_tramos_de_aire(eventos, clave_aire, ambito_comodo):
    """
    Tramos de ejecución continua (sin pausa suficiente) por instrumento, con
    duración real y ponderada (ver LIMITES_AIRE/MULT_DINAMICA en
    configuracion_ejecucion.py) contra los umbrales aviso/critico de ese
    instrumento. Un silencio corta el tramo si dura >= PAUSA_MINIMA_RESPIRO,
    salvo para oboe/corno_ingles una vez que el tramo ya superó su propio
    umbral de aviso, donde el corte exige >= PAUSA_EXHALACION (el aire
    sobrante tarda más en exhalarse).
    """
    limites = LIMITES_AIRE[clave_aire]
    es_oboe_o_corno_ingles = clave_aire in ('oboe', 'corno_ingles')
    mult_grave = limites.get('mult_grave', 1.0)
    mult_agudo = limites.get('mult_agudo', 1.0)  # solo trompeta lo define distinto de 1.0
    corte_grave, corte_agudo = _cortes_registro(ambito_comodo)

    tramos = []
    notas_tramo = []
    ya_supero_aviso = False

    def cerrar():
        if not notas_tramo:
            return None
        duracion_real = sum(n['duracion_segundos'] for n in notas_tramo)
        duracion_ponderada = sum(n['ponderada'] for n in notas_tramo)
        if duracion_ponderada >= limites['critico']:
            nivel = 'critico'
        elif duracion_ponderada >= limites['aviso']:
            nivel = 'aviso'
        else:
            nivel = 'ok'
        return {
            'compas_desde': notas_tramo[0]['compas'],
            'compas_hasta': notas_tramo[-1]['compas'],
            'duracion_segundos': duracion_real,
            'duracion_ponderada': duracion_ponderada,
            'umbral_aviso': limites['aviso'],
            'umbral_critico': limites['critico'],
            'nivel': nivel,
        }

    for ev in eventos:
        if ev['tipo'] == 'silencio':
            if not notas_tramo:
                continue
            requiere = PAUSA_EXHALACION if (es_oboe_o_corno_ingles and ya_supero_aviso) else PAUSA_MINIMA_RESPIRO
            if ev['duracion_segundos'] >= requiere:
                cerrado = cerrar()
                if cerrado:
                    tramos.append(cerrado)
                notas_tramo = []
                ya_supero_aviso = False
            continue

        ps = ev['pitch'].ps
        mult_registro = 1.0
        if ps <= corte_grave:
            mult_registro = mult_grave
        elif clave_aire == 'trompeta' and ps >= corte_agudo:
            mult_registro = mult_agudo
        mult_din = MULT_DINAMICA.get(ev['dinamica'], 1.0)
        ponderada = ev['duracion_segundos'] / (mult_registro * mult_din)
        notas_tramo.append({**ev, 'ponderada': ponderada})

        if sum(n['ponderada'] for n in notas_tramo) >= limites['aviso']:
            ya_supero_aviso = True

    cerrado = cerrar()
    if cerrado:
        tramos.append(cerrado)

    tramos.sort(key=lambda t: t['duracion_ponderada'], reverse=True)
    return tramos[:TOP_N_TRAMOS_AIRE]


def calcular_densidad_ritmica(eventos):
    """
    Notas (ataques, no silencios) por segundo en ventana móvil de
    VENTANA_DENSIDAD_SEGUNDOS, paso PASO_VENTANA_SEGUNDOS -- devuelve los picos
    más densos, uno por compás distinto (si varias ventanas consecutivas caen
    en el mismo compás de inicio, se cuenta una sola vez: el pico real, no un
    duplicado del mismo tramo). Ventana en SEGUNDOS reales, no compases --
    confirmado con el usuario, un pasaje suena más denso a tempo rápido que a
    tempo lento aunque la partitura se vea igual.

    Dos punteros en vez de recorrer todas las notas en cada ventana: O(notas +
    ventanas) en vez de O(notas × ventanas), importa en una obra larga.
    """
    ataques = [e for e in eventos if e['tipo'] == 'nota']
    if not ataques:
        return []
    offsets = [a['offset_segundos'] for a in ataques]
    duracion_total = offsets[-1] + ataques[-1]['duracion_segundos']

    picos = []
    izq = der = 0
    n = len(ataques)
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
                'compas': ataques[izq]['compas'],
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
    """Intervalo (semitonos) entre ataques consecutivos de la parte (acordes
    -> nota más aguda, mismo criterio que _eventos_sonantes_por_compas en
    views.py). 'Mayor a una octava' es estrictamente > 12 semitonos -- una
    octava exacta no cuenta."""
    ataques = [e for e in eventos if e['tipo'] == 'nota']
    saltos = [
        {
            'semitonos': abs(actual['pitch'].ps - anterior['pitch'].ps),
            'compas_desde': anterior['compas'],
            'compas_hasta': actual['compas'],
        }
        for anterior, actual in zip(ataques, ataques[1:])
    ]
    if not saltos:
        return {'maximo_semitonos': 0, 'cantidad_mayor_octava': 0, 'mas_grandes': []}

    return {
        'maximo_semitonos': max(s['semitonos'] for s in saltos),
        'cantidad_mayor_octava': sum(1 for s in saltos if s['semitonos'] > UMBRAL_SALTO_SEMITONOS),
        'mas_grandes': sorted(saltos, key=lambda s: s['semitonos'], reverse=True)[:TOP_N_SALTOS],
    }


def detectar_cruce_dinamica_registro(eventos, ambito_comodo):
    """Notas en zona extrema (tercio grave o agudo del ámbito cómodo) con
    dinámica vigente extrema (f/ff/fff o pp/ppp) en ese punto -- cualquiera de
    las 4 combinaciones registro×dinámica cuenta, no solo la más obvia.

    Agrupa en rangos de compases contiguos con el mismo (registro, dinámica)
    -- mismo criterio que detectar_duplicaciones_verificadas en views.py. Sin
    esto, un pasaje sostenido de 30 notas en ff daba 30 entradas casi
    idénticas (bug real encontrado al inspeccionar la salida JSON real antes
    de cerrar esta fase)."""
    corte_grave, corte_agudo = _cortes_registro(ambito_comodo)
    coincidencias = []
    for ev in eventos:
        if ev['tipo'] != 'nota':
            continue
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


def _agregar_aviso(avisos_acumulados, tipo, instrumento, valor=None):
    """Agrupa por (tipo, instrumento, valor) con un contador -- nunca una
    entrada por ocurrencia/nota."""
    clave = (tipo, instrumento, valor)
    if clave not in avisos_acumulados:
        avisos_acumulados[clave] = {'tipo': tipo, 'instrumento': instrumento, 'valor': valor, 'ocurrencias': 0}
    avisos_acumulados[clave]['ocurrencias'] += 1


def calcular_metricas_de_parte(part, part_name, avisos_acumulados, tiempos_por_id):
    """
    Punto de entrada por instrumento -- se llama desde DENTRO del loop
    por-parte que ya existe en _generar_analisis_orquestacion (views.py),
    justo al lado de evaluar_viabilidad_instrumental, reutilizando el
    heartbeat que ya se emite ahí (no hay una segunda pasada sobre `parts`).

    `tiempos_por_id` viene de construir_mapa_tiempos(score), calculado UNA
    sola vez para toda la obra antes del loop (ver el comentario grande ahí --
    nunca se recalcula por parte, un part.flatten().secondsMap propio no ve el
    tempo de las demás partes).

    Devuelve (resultado_de_esta_parte, hubo_dynamic) -- el segundo valor se
    usa en el llamador para decidir el aviso de nivel-obra "este archivo no
    trae dinámica" (típico de MIDI).
    """
    eventos, dinamicas_no_reconocidas, hubo_dynamic = _construir_eventos_parte(part, tiempos_por_id)
    for valor in dinamicas_no_reconocidas:
        _agregar_aviso(avisos_acumulados, 'dinamica_no_reconocida', part_name, valor)

    canonico, _nivel_resolucion = resolver_instrumento(part, part_name)

    resultado = {
        'tramos_aire': [],
        'densidad_ritmica': calcular_densidad_ritmica(eventos),
        'saltos_melodicos': detectar_saltos_melodicos(eventos),
        'cruce_dinamica_registro': [],
    }

    if canonico is None:
        _agregar_aviso(avisos_acumulados, 'instrumento_no_reconocido', part_name)
        return resultado, hubo_dynamic

    ambito_comodo = RANGOS_COMODOS[canonico]
    resultado['cruce_dinamica_registro'] = detectar_cruce_dinamica_registro(eventos, ambito_comodo)

    clave_aire = CANONICO_A_CLAVE_AIRE.get(canonico)
    if clave_aire is not None:
        resultado['tramos_aire'] = detectar_tramos_de_aire(eventos, clave_aire, ambito_comodo)
    # clave_aire None: instrumento reconocido (cuerdas/piano/percusión/
    # guitarra/arpa) que correctamente no respira -- sin alerta, sin aviso.

    return resultado, hubo_dynamic
