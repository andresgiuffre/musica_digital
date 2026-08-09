"""Herramienta de curación LOCAL -- nunca se ejecuta en PythonAnywhere.

Toma partituras de cámara multi-parte (MusicXML) y genera una reducción de
piano de cada una: la voz melódica como mano derecha, el resto colapsado
como mano izquierda, con limpieza (anti-duplicación, des-densificación,
plegado de registro) para que el resultado sea legible. Pensada para curar
en MuseScore y subir después como FragmentoOrquestacion. No toca la base de
datos ni ningún modelo/vista del sitio.

Uso: python manage.py reducir_a_piano <carpeta_entrada> <carpeta_salida>
"""
import copy
import datetime
import json
from pathlib import Path

import music21
from django.core.management.base import BaseCommand, CommandError

from ._comun import EXTENSIONES_VALIDAS, EscritorLog, identificar_voces, normalizar_a_score

SEMITONOS_DUPLICACION = 0  # comparación por CLASE de altura -- cubre unísono y cualquier octava a la vez
AMBITO_MAXIMO_ACORDE_SEMITONOS = 24
TOLERANCIA_OFFSET = 1e-4  # redondeo de floats al agrupar onsets


class Command(BaseCommand):
    help = (
        "Genera una reducción de piano (mano derecha=melodía, mano izquierda=resto "
        "colapsado y limpiado) de cada partitura de cámara en la carpeta de entrada. "
        "Herramienta de curación local -- no toca la base de datos."
    )

    def add_arguments(self, parser):
        parser.add_argument('carpeta_entrada', type=str)
        parser.add_argument('carpeta_salida', type=str)

    def handle(self, *args, **options):
        entrada = Path(options['carpeta_entrada'])
        salida = Path(options['carpeta_salida'])
        if not entrada.is_dir():
            raise CommandError(f"No existe la carpeta de entrada: {entrada}")
        salida.mkdir(parents=True, exist_ok=True)

        archivos = sorted(p for p in entrada.iterdir() if p.suffix.lower() in EXTENSIONES_VALIDAS)
        if not archivos:
            self.stdout.write(self.style.WARNING(
                f"No se encontraron piezas ({', '.join(sorted(EXTENSIONES_VALIDAS))}) en {entrada}"
            ))
            return

        self._log = EscritorLog(self, salida)
        self._log.log('info', f"Corrida iniciada {datetime.datetime.now().isoformat(timespec='seconds')} -- {len(archivos)} archivo(s) encontrados.")

        piezas_ok = 0
        piezas_falladas = 0
        for archivo in archivos:
            self._log.log('info', f"Procesando {archivo.name}...")
            try:
                if self._procesar_pieza(archivo, salida):
                    piezas_ok += 1
                else:
                    piezas_falladas += 1
            except Exception as e:
                piezas_falladas += 1
                self._log.log('error', f"{archivo.name}: fallo por completo, se omite ({e.__class__.__name__}: {e})")

        self._log.log('info', f"Listo: {piezas_ok} pieza(s) reducida(s), {piezas_falladas} fallida(s)/omitida(s).")
        self._log.cerrar()

    # --- por pieza ---

    def _procesar_pieza(self, archivo, carpeta_salida):
        nombre_pieza = archivo.stem
        original = normalizar_a_score(music21.converter.parse(str(archivo)))

        partes = list(original.parts)
        if len(partes) < 2:
            self._log.log('info', f"{nombre_pieza}: tiene {len(partes)} parte(s), no hay nada que reducir -- se omite.")
            return False

        # Sounding pitch para TODO el score, antes de identificar melodía/acompañamiento
        # -- <pitch> en MusicXML es siempre lo ESCRITO, así que declarar
        # atSoundingPitch=False acá es correcto sea cual sea el instrumento. Sin esto,
        # toSoundingPitch() no transpone nada (comprobado empíricamente).
        original.atSoundingPitch = False
        original = original.toSoundingPitch()
        partes = list(original.parts)

        voz_melodia, partes_acompanamiento = identificar_voces(partes)
        if not partes_acompanamiento:
            self._log.log('info', f"{nombre_pieza}: solo una parte suena, no hay acompañamiento -- se omite.")
            return False

        nombre_melodia = voz_melodia.partName or 'Melodia'
        nombres_acompanamiento = [p.partName or f'Parte{i}' for i, p in enumerate(partes_acompanamiento)]

        eventos_mi_crudos = self._extraer_eventos(partes_acompanamiento)
        if not eventos_mi_crudos:
            self._log.log('info', f"{nombre_pieza}: el acompañamiento no tiene notas -- se omite.")
            return False

        eventos_melodia = self._extraer_eventos([voz_melodia])

        grilla = self._construir_grilla(eventos_mi_crudos, eventos_melodia)
        grilla, resumen_limpieza = self._limpiar_grilla(grilla, eventos_melodia)

        cambios_atributos = self._recolectar_cambios_atributos(original)

        rh = self._construir_mano_derecha(voz_melodia, cambios_atributos)
        lh = self._construir_mano_izquierda(grilla, cambios_atributos, original)
        if lh is None:
            self._log.log('warning', f"{nombre_pieza}: la mano izquierda quedó vacía tras la limpieza -- se omite.")
            return False

        tempo_marks = list(original.flatten().getElementsByClass(music21.tempo.MetronomeMark))
        for mm in tempo_marks:
            offset_global = mm.getOffsetInHierarchy(original)
            self._insertar_en_offset(rh, offset_global, copy.deepcopy(mm))

        score_final = music21.stream.Score()
        score_final.insert(0, rh)
        score_final.insert(0, lh)
        grupo = music21.layout.StaffGroup([rh, lh], symbol='brace', barTogether=True)
        score_final.insert(0, grupo)

        xml_bytes = music21.musicxml.m21ToXml.GeneralObjectExporter(score_final).parse()
        if not self._verificar_reparseo(xml_bytes, nombre_pieza):
            return False

        carpeta_salida.mkdir(parents=True, exist_ok=True)
        (carpeta_salida / f"{nombre_pieza}_piano.musicxml").write_bytes(xml_bytes)

        metadatos = {
            'pieza_origen': nombre_pieza,
            'partes_originales': [p.partName or f'Parte{i}' for i, p in enumerate(partes)],
            'parte_melodia': nombre_melodia,
            'partes_acompanamiento': nombres_acompanamiento,
            'resumen_limpieza': resumen_limpieza,
            'notas': self._trazabilidad(rh, lh, grilla, eventos_melodia, nombre_melodia),
        }
        (carpeta_salida / f"{nombre_pieza}_piano.json").write_text(
            json.dumps(metadatos, ensure_ascii=False, indent=2), encoding='utf-8'
        )

        self._log.log('info', (
            f"{nombre_pieza}: reducción generada -- melodía={nombre_melodia}, "
            f"acompañamiento={nombres_acompanamiento}, limpieza={resumen_limpieza}"
        ))
        return True

    # --- extracción de eventos por parte (offset global, duración, pitch, origen) ---

    def _extraer_eventos(self, partes):
        eventos = []
        for parte in partes:
            nombre = parte.partName or 'Parte'
            parte_st = parte.stripTies()
            for n in parte_st.recurse().notes:
                offset_global = n.getOffsetInHierarchy(parte_st)
                duracion = n.duration.quarterLength
                if duracion <= 0:
                    continue
                for p in n.pitches:
                    eventos.append({
                        'offset': offset_global,
                        'duracion': duracion,
                        'pitch': p,
                        'origen': nombre,
                    })
        return eventos

    # --- construcción de la grilla de la mano izquierda (onset merge, con origen) ---

    def _construir_grilla(self, eventos_mi, eventos_melodia):
        puntos_onset = sorted({round(ev['offset'], 6) for ev in eventos_mi})
        fin_pieza = max(ev['offset'] + ev['duracion'] for ev in eventos_mi)

        grilla = []
        for i, t_inicio in enumerate(puntos_onset):
            t_fin = puntos_onset[i + 1] if i + 1 < len(puntos_onset) else fin_pieza
            if t_fin - t_inicio <= TOLERANCIA_OFFSET:
                continue

            activos = [
                ev for ev in eventos_mi
                if ev['offset'] <= t_inicio + TOLERANCIA_OFFSET < ev['offset'] + ev['duracion']
            ]

            # Alturas idénticas de distintas partes se colapsan a una sola nota del
            # acorde, pero se listan todos los orígenes que la sostienen.
            por_pitch = {}
            for ev in activos:
                clave = ev['pitch'].nameWithOctave
                if clave not in por_pitch:
                    por_pitch[clave] = {'pitch': ev['pitch'], 'origenes': set()}
                por_pitch[clave]['origenes'].add(ev['origen'])

            grilla.append({
                'offset': t_inicio,
                'duracion': t_fin - t_inicio,
                'notas': [
                    {'pitch': v['pitch'], 'origenes': sorted(v['origenes'])}
                    for v in por_pitch.values()
                ],
            })
        return grilla

    def _melodia_en_offset(self, eventos_melodia, offset):
        for ev in eventos_melodia:
            if ev['offset'] <= offset + TOLERANCIA_OFFSET < ev['offset'] + ev['duracion']:
                return ev['pitch']
        return None

    # --- limpieza: Regla 1 (anti-duplicación) + Regla 3 (plegado) + Regla 2 (fusión) ---

    def _limpiar_grilla(self, grilla, eventos_melodia):
        resumen = {'notas_eliminadas_por_doblaje': 0, 'notas_plegadas': 0, 'tramos_fusionados': 0}

        # Regla 1: anti-duplicación contra la melodía (unísono u octava == misma pitch class)
        for tramo in grilla:
            melodia_pitch = self._melodia_en_offset(eventos_melodia, tramo['offset'])
            if melodia_pitch is None:
                continue
            antes = len(tramo['notas'])
            tramo['notas'] = [n for n in tramo['notas'] if n['pitch'].pitchClass != melodia_pitch.pitchClass]
            resumen['notas_eliminadas_por_doblaje'] += antes - len(tramo['notas'])

        # Regla 3: cruce de voces (plegado de una octava) + ámbito excesivo (plegado hacia adentro)
        for tramo in grilla:
            melodia_pitch = self._melodia_en_offset(eventos_melodia, tramo['offset'])
            if melodia_pitch is not None:
                for n in tramo['notas']:
                    if n['pitch'].ps > melodia_pitch.ps:
                        n['pitch'] = self._transportar_octava(n['pitch'], -1)
                        resumen['notas_plegadas'] += 1

            intentos = 0
            while tramo['notas'] and intentos < 12:
                ps_vals = [n['pitch'].ps for n in tramo['notas']]
                if max(ps_vals) - min(ps_vals) <= AMBITO_MAXIMO_ACORDE_SEMITONOS:
                    break
                nota_mas_aguda = max(tramo['notas'], key=lambda n: n['pitch'].ps)
                nueva = self._transportar_octava(nota_mas_aguda['pitch'], -1)
                if nueva.ps <= min(ps_vals):
                    self._log.log('warning', (
                        f"tramo en offset {tramo['offset']}: no se pudo plegar del todo el ámbito "
                        f"({max(ps_vals) - min(ps_vals)} semitonos residuales)."
                    ))
                    break
                nota_mas_aguda['pitch'] = nueva
                resumen['notas_plegadas'] += 1
                intentos += 1

        # Vacíos post-Regla-1: se resuelven fusionándolos con el tramo anterior (más
        # abajo, en la fusión general) salvo que sean el primer tramo de la pieza, en
        # cuyo caso no hay nada que extender y quedan como silencio real.
        grilla_fusionada = []
        for tramo in grilla:
            if grilla_fusionada:
                anterior = grilla_fusionada[-1]
                mismo_contenido = tramo['notas'] and self._mismo_contenido(anterior['notas'], tramo['notas'])
                vacio_para_extender = not tramo['notas']
                if mismo_contenido or vacio_para_extender:
                    anterior['duracion'] += tramo['duracion']
                    if vacio_para_extender:
                        pass  # el contenido sonoro sigue siendo el del tramo anterior
                    resumen['tramos_fusionados'] += 1
                    continue
            grilla_fusionada.append(tramo)

        return grilla_fusionada, resumen

    def _mismo_contenido(self, notas_a, notas_b):
        clave = lambda notas: frozenset(n['pitch'].nameWithOctave for n in notas)
        return clave(notas_a) == clave(notas_b)

    def _transportar_octava(self, pitch, direccion):
        nuevo = copy.deepcopy(pitch)
        nuevo.octave = (nuevo.octave or 4) + direccion
        return nuevo

    # --- cambios de armadura/compás a lo largo de la pieza (para sembrar ambas manos) ---

    def _recolectar_cambios_atributos(self, original):
        referencia = original.parts[0]
        cambios = []
        for clase in (music21.meter.TimeSignature, music21.key.KeySignature):
            for el in referencia.flatten().getElementsByClass(clase):
                cambios.append((el.getOffsetInHierarchy(referencia), el))
        cambios.sort(key=lambda t: t[0])
        return cambios

    def _insertar_en_offset(self, parte_plana_o_con_medidas, offset, elemento):
        parte_plana_o_con_medidas.insert(offset, elemento)

    # --- mano derecha: copia directa de la melodía, a altura real ---

    def _construir_mano_derecha(self, voz_melodia, cambios_atributos):
        rh = music21.stream.PartStaff()
        rh.partName = 'Piano'
        rh.insert(0, music21.instrument.Piano())
        for m in voz_melodia.getElementsByClass(music21.stream.Measure):
            rh.append(copy.deepcopy(m))

        primera_medida = rh.getElementsByClass(music21.stream.Measure)[0]
        if not primera_medida.getElementsByClass(music21.clef.Clef):
            primera_medida.insert(0, music21.clef.TrebleClef())
        return rh

    # --- mano izquierda: se construye plana y se barra con makeMeasures+makeTies ---

    def _construir_mano_izquierda(self, grilla, cambios_atributos, original):
        if not grilla:
            return None

        plano = music21.stream.Stream()
        plano.insert(0, music21.clef.BassClef())
        for offset, elemento in cambios_atributos:
            plano.insert(offset, copy.deepcopy(elemento))
        if not any(isinstance(el, music21.meter.TimeSignature) for _, el in cambios_atributos):
            plano.insert(0, music21.meter.TimeSignature('4/4'))

        for tramo in grilla:
            if tramo['notas']:
                el = music21.chord.Chord([n['pitch'] for n in tramo['notas']], quarterLength=tramo['duracion'])
            else:
                el = music21.note.Rest(quarterLength=tramo['duracion'])
            plano.insert(tramo['offset'], el)

        medidas = plano.makeMeasures()
        medidas.makeTies(inPlace=True)

        lh = music21.stream.PartStaff()
        for m in medidas.getElementsByClass(music21.stream.Measure):
            lh.append(m)
        return lh

    # --- trazabilidad ---
    #
    # Se deriva de rh/lh DESPUÉS de makeMeasures+makeTies (no de grilla/eventos_melodia
    # directamente): un tramo fusionado por la Regla 2 que cruza compases va a aparecer
    # en el MusicXML final como varios segmentos ligados (uno por compás) -- exactamente
    # las unidades que _notas_piano_para_ejercicio va a leer en el ejercicio de pintado.
    # El JSON tiene que reportar esas mismas unidades, no mi agrupación interna previa a
    # la fusión, si va a servir de "respuesta oficial" para comparar contra lo pintado.
    # compas/offset/duracion salen directo del objeto ya barreado (siempre correctos);
    # origen se recupera correlacionando el offset+pitch del segmento contra la fuente
    # (grilla o eventos_melodia) cuyo rango [offset, offset+duracion) lo contiene --
    # sin ambigüedad porque se compara contra mis propias estructuras internas, no a
    # ciegas contra las parts originales.

    def _trazabilidad(self, rh, lh, grilla, eventos_melodia, nombre_melodia):
        fuentes_rh = [
            (ev['offset'], ev['offset'] + ev['duracion'], ev['pitch'].nameWithOctave, [nombre_melodia])
            for ev in eventos_melodia
        ]
        fuentes_lh = [
            (tramo['offset'], tramo['offset'] + tramo['duracion'], n['pitch'].nameWithOctave, n['origenes'])
            for tramo in grilla for n in tramo['notas']
        ]
        notas = self._trazar_mano(rh, 'derecha', fuentes_rh) + self._trazar_mano(lh, 'izquierda', fuentes_lh)
        notas.sort(key=lambda n: (n['offset'], n['mano']))
        return notas

    def _trazar_mano(self, parte, nombre_mano, fuentes):
        resultado = []
        for n in parte.recurse().notes:
            offset_global = round(n.getOffsetInHierarchy(parte), 6)
            for p in n.pitches:
                resultado.append({
                    'mano': nombre_mano,
                    'compas': n.measureNumber,
                    'offset': offset_global,
                    'duracion_ql': n.duration.quarterLength,
                    'pitch': p.nameWithOctave,
                    'origen': self._buscar_origen(fuentes, offset_global, p.nameWithOctave),
                })
        return resultado

    def _buscar_origen(self, fuentes, offset, nombre_pitch):
        for inicio, fin, pitch_nombre, origenes in fuentes:
            if pitch_nombre == nombre_pitch and inicio - TOLERANCIA_OFFSET <= offset < fin + TOLERANCIA_OFFSET:
                return origenes
        return ['desconocido']

    # --- verificación por reparseo ---

    def _verificar_reparseo(self, xml_bytes, nombre_pieza):
        try:
            reparseado = music21.converter.parseData(xml_bytes, format='musicxml')
        except Exception as e:
            self._log.log('warning', f"{nombre_pieza}: falló el reparseo de verificación ({e}) -- se descarta.")
            return False

        partes = list(reparseado.parts)
        if len(partes) != 2:
            self._log.log('warning', f"{nombre_pieza}: se esperaban 2 pentagramas (PartStaff), el reparseo tiene {len(partes)} -- se descarta.")
            return False

        rh, lh = partes
        clefs_rh = list(rh.flatten().getElementsByClass(music21.clef.Clef))
        clefs_lh = list(lh.flatten().getElementsByClass(music21.clef.Clef))
        if not clefs_rh or not isinstance(clefs_rh[0], music21.clef.TrebleClef):
            self._log.log('warning', f"{nombre_pieza}: la mano derecha no tiene clave de sol explícita -- se descarta.")
            return False
        if not clefs_lh or not isinstance(clefs_lh[0], music21.clef.BassClef):
            self._log.log('warning', f"{nombre_pieza}: la mano izquierda no tiene clave de fa explícita -- se descarta.")
            return False

        for nombre_mano, parte in (('derecha', rh), ('izquierda', lh)):
            if not list(parte.recurse().notes):
                self._log.log('warning', f"{nombre_pieza}: la mano {nombre_mano} no tiene ninguna nota -- se descarta.")
                return False
            for m in parte.getElementsByClass(music21.stream.Measure):
                suma = sum(el.duration.quarterLength for el in m.notesAndRests)
                esperado = m.barDuration.quarterLength
                if abs(suma - esperado) > TOLERANCIA_OFFSET:
                    self._log.log('warning', (
                        f"{nombre_pieza}: mano {nombre_mano}, compás {m.number}, suma {suma} "
                        f"!= esperado {esperado} -- se descarta."
                    ))
                    return False

        return True
