"""Herramienta de curación LOCAL -- nunca se ejecuta en PythonAnywhere.

Corta piezas largas (MusicXML) en excerpts de 8-16 compases, candidatos a
ejercicios de solfeo, con métricas de dificultad calculadas de forma
determinística vía music21. No toca la base de datos ni ningún modelo/vista
del sitio -- es un script de archivos, autocontenido a propósito.

Uso: python manage.py triturar_piezas <carpeta_entrada> <carpeta_salida>
"""
import copy
import csv
import datetime
import json
from pathlib import Path

import music21
from django.core.management.base import BaseCommand, CommandError

from ._comun import EXTENSIONES_VALIDAS, EscritorLog, identificar_voces, normalizar_a_score

MIN_COMPASES = 8
MAX_COMPASES = 16

QL_NOTA_LARGA_CADENCIA = 2.0   # blanca o más -- heurística 1 (cadencia)
QL_NOTA_LARGA_FRASE = 3.0      # blanca con puntillo o más -- heurística 2 (fin de frase)
SEMITONOS_SALTO = 3            # a partir de acá un intervalo cuenta como "salto", no grado conjunto
BEAT_STRENGTH_SINCOPA = 0.25   # posición métrica débil -- proxy barato de síncopa/contratiempo

# Umbrales de dificultad -- (límite "fácil", límite "media"); por encima del
# segundo valor puntúa "difícil". Un solo lugar, ajustables sin tocar la lógica.
UMBRALES_DIFICULTAD = {
    'ambito_semitonos': (12, 19),
    'salto_maximo_semitonos': (4, 7),
    'proporcion_saltos': (0.15, 0.35),
    'accidentales_fuera_armadura': (0, 2),
    'variedad_ritmica': (2, 4),
}
PUNTAJE_A_ETIQUETA = [(3, 'facil'), (6, 'media')]  # > 6 -> 'dificil'

COLUMNAS_CSV = [
    'pieza', 'excerpt', 'compas_inicio', 'compas_fin', 'compases',
    'tipo_corte', 'corte_forzado', 'bajo_confirma_cadencia', 'incluye_anacrusa',
    'nota_mas_grave', 'nota_mas_aguda', 'ambito_semitonos',
    'grados_conjuntos', 'saltos', 'salto_maximo_semitonos', 'proporcion_saltos',
    'accidentales_fuera_armadura', 'variedad_ritmica', 'sincopas_aprox',
    'tonica', 'modo', 'confianza_tonalidad',
    'compas_metrica', 'cambios_compas',
    'dificultad_estimada', 'puntaje_dificultad',
]


class Command(BaseCommand):
    help = (
        "Corta piezas largas en excerpts de 8-16 compases, candidatos a ejercicios "
        "de solfeo. Herramienta de curación local -- no toca la base de datos."
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

        resumen_path = salida / 'resumen.csv'
        piezas_ok = 0
        piezas_falladas = 0
        excerpts_generados = 0

        with open(resumen_path, 'w', newline='', encoding='utf-8') as f_csv:
            csv_writer = csv.DictWriter(f_csv, fieldnames=COLUMNAS_CSV)
            csv_writer.writeheader()

            for archivo in archivos:
                self._log.log('info', f"Procesando {archivo.name}...")
                try:
                    n = self._procesar_pieza(archivo, salida, csv_writer)
                    excerpts_generados += n
                    piezas_ok += 1
                except Exception as e:
                    piezas_falladas += 1
                    self._log.log('error', f"{archivo.name}: fallo por completo, se omite ({e.__class__.__name__}: {e})")

        self._log.log('info', (
            f"Listo: {piezas_ok} pieza(s) procesada(s), {piezas_falladas} fallida(s), "
            f"{excerpts_generados} excerpt(s) generado(s)."
        ))
        self._log.cerrar()

    # --- por pieza ---

    def _procesar_pieza(self, archivo, carpeta_salida, csv_writer):
        original = normalizar_a_score(music21.converter.parse(str(archivo)))

        nombre_pieza = archivo.stem
        partes = list(original.parts)
        if not partes:
            raise ValueError("la pieza no tiene partes reconocibles")

        voz_superior, resto = identificar_voces(partes)
        voz_bajo = resto[0] if resto else None

        medidas_voz_superior = list(voz_superior.getElementsByClass(music21.stream.Measure))
        if not medidas_voz_superior:
            raise ValueError("la voz superior no tiene compases")

        primer_compas = medidas_voz_superior[0]
        hay_anacrusa = primer_compas.number == 0 or primer_compas.paddingLeft > 0
        if hay_anacrusa:
            self._log.log('info', (
                f"{nombre_pieza}: compás de anacrusa detectado (número={primer_compas.number}, "
                f"paddingLeft={primer_compas.paddingLeft}) -- se incorpora al inicio del excerpt 1."
            ))

        numeros_compases = [m.number for m in medidas_voz_superior if m.number > 0]
        if not numeros_compases:
            raise ValueError("la pieza no tiene compases numerados > 0 (¿solo anacrusa?)")
        total_compases = max(numeros_compases)

        if total_compases < MIN_COMPASES:
            self._log.log('info', f"{nombre_pieza}: solo {total_compases} compás(es) (< {MIN_COMPASES}), se omite la pieza completa.")
            return 0

        puntos = self._detectar_puntos_de_corte(nombre_pieza, voz_superior, voz_bajo, total_compases)

        n_generados = 0
        for i, punto in enumerate(puntos):
            indice = i + 1
            compas_inicio = 0 if (i == 0 and hay_anacrusa) else punto['compas_inicio']
            ok = self._construir_y_guardar_excerpt(
                original=original,
                nombre_pieza=nombre_pieza,
                indice=indice,
                compas_inicio=compas_inicio,
                compas_fin=punto['compas_fin'],
                tipo_corte=punto['tipo'],
                bajo_confirma_cadencia=punto['bajo_confirma_cadencia'],
                incluye_anacrusa=(i == 0 and hay_anacrusa),
                carpeta_salida=carpeta_salida,
                csv_writer=csv_writer,
            )
            if ok:
                n_generados += 1
        return n_generados

    # --- segmentación ---

    def _detectar_puntos_de_corte(self, nombre_pieza, voz_superior, voz_bajo, total_compases):
        voz_superior_st = voz_superior.stripTies()
        voz_bajo_st = voz_bajo.stripTies() if voz_bajo is not None else None

        puntos = []
        inicio = 1
        while inicio <= total_compases:
            fin_ventana = min(inicio + MAX_COMPASES - 1, total_compases)
            largo_disponible = fin_ventana - inicio + 1

            if largo_disponible < MIN_COMPASES:
                if puntos:
                    anterior = puntos[-1]
                    largo_anterior = anterior['compas_fin'] - anterior['compas_inicio'] + 1
                    if largo_anterior + largo_disponible <= MAX_COMPASES:
                        self._log.log('info', (
                            f"{nombre_pieza}: resto final de {largo_disponible} compás(es) "
                            f"(compases {inicio}-{fin_ventana}) fusionado al excerpt anterior "
                            f"(quedó en {largo_anterior + largo_disponible} compases)."
                        ))
                        anterior['compas_fin'] = fin_ventana
                    else:
                        self._log.log('info', (
                            f"{nombre_pieza}: resto final de {largo_disponible} compás(es) "
                            f"(compases {inicio}-{fin_ventana}) descartado -- no entra en el "
                            f"excerpt anterior sin superar {MAX_COMPASES} compases."
                        ))
                else:
                    self._log.log('info', (
                        f"{nombre_pieza}: resto de {largo_disponible} compás(es) "
                        f"(compases {inicio}-{fin_ventana}) descartado -- pieza sin ningún "
                        f"excerpt válido de {MIN_COMPASES}+ compases."
                    ))
                break

            punto = self._buscar_punto_en_ventana(voz_superior_st, voz_bajo_st, inicio, fin_ventana)
            punto['compas_inicio'] = inicio
            puntos.append(punto)
            inicio = punto['compas_fin'] + 1

        return puntos

    def _buscar_punto_en_ventana(self, voz_superior, voz_bajo, inicio, fin_ventana):
        for compas_candidato in range(inicio + MIN_COMPASES - 1, fin_ventana + 1):
            bajo_confirma = self._evaluar_cadencia(voz_superior, voz_bajo, inicio, compas_candidato)
            if bajo_confirma is not None:
                return {'compas_fin': compas_candidato, 'tipo': 'cadencia', 'bajo_confirma_cadencia': bajo_confirma}

        for compas_candidato in range(inicio + MIN_COMPASES - 1, fin_ventana + 1):
            if self._evaluar_fin_de_frase(voz_superior, compas_candidato):
                return {'compas_fin': compas_candidato, 'tipo': 'fin_frase', 'bajo_confirma_cadencia': False}

        return {'compas_fin': fin_ventana, 'tipo': 'forzado', 'bajo_confirma_cadencia': False}

    def _evaluar_cadencia(self, voz_superior, voz_bajo, inicio, compas_candidato):
        """None si no hay cadencia en ese compás; si no, True/False según si el bajo confirma V-I."""
        notas_candidatas = [
            n for n in voz_superior.recurse().notes
            if n.measureNumber == compas_candidato and n.duration.quarterLength >= QL_NOTA_LARGA_CADENCIA
        ]
        if not notas_candidatas:
            return None

        try:
            ventana = voz_superior.measures(inicio, compas_candidato)
            key_local = ventana.analyze('key')
            tonica_pc = key_local.pitchFromDegree(1).pitchClass
            dominante_pc = key_local.pitchFromDegree(5).pitchClass
        except Exception:
            return None

        for nota in notas_candidatas:
            pcs = [p.pitchClass for p in nota.pitches]
            if tonica_pc in pcs or dominante_pc in pcs:
                if voz_bajo is not None:
                    return self._confirmar_bajo_v_i(voz_bajo, compas_candidato, tonica_pc, dominante_pc)
                return False
        return None

    def _confirmar_bajo_v_i(self, voz_bajo, compas_candidato, tonica_pc, dominante_pc):
        notas_actual = [n for n in voz_bajo.recurse().notes if n.measureNumber == compas_candidato]
        notas_anterior = [n for n in voz_bajo.recurse().notes if n.measureNumber == compas_candidato - 1]
        if not notas_actual or not notas_anterior:
            return False
        pcs_actual = [p.pitchClass for p in notas_actual[0].pitches]
        pcs_anterior = [p.pitchClass for p in notas_anterior[-1].pitches]
        return tonica_pc in pcs_actual and dominante_pc in pcs_anterior

    def _evaluar_fin_de_frase(self, voz_superior, compas_candidato):
        elementos = [
            el for el in voz_superior.recurse().notesAndRests
            if el.measureNumber == compas_candidato and el.duration.quarterLength >= QL_NOTA_LARGA_FRASE
        ]
        return len(elementos) > 0

    # --- construcción + verificación + escritura de cada excerpt ---

    def _construir_y_guardar_excerpt(self, original, nombre_pieza, indice, compas_inicio, compas_fin,
                                      tipo_corte, bajo_confirma_cadencia, incluye_anacrusa,
                                      carpeta_salida, csv_writer):
        excerpt = original.measures(compas_inicio, compas_fin)

        for parte_original, parte_excerpt in zip(original.parts, excerpt.parts):
            self._asegurar_atributos_heredados(parte_original, parte_excerpt, compas_inicio)

        try:
            voz_sup_ex, _ = identificar_voces(list(excerpt.parts))
        except ValueError as e:
            self._log.log('warning', f"{nombre_pieza} ex{indice:02d} (compases {compas_inicio}-{compas_fin}): {e} -- se omite.")
            return False

        metricas = self._calcular_metricas(excerpt, voz_sup_ex)

        xml_bytes = music21.musicxml.m21ToXml.GeneralObjectExporter(excerpt).parse()
        if not self._verificar_reparseo(xml_bytes, compas_inicio, compas_fin, nombre_pieza, indice):
            return False

        carpeta_pieza = carpeta_salida / nombre_pieza
        carpeta_pieza.mkdir(parents=True, exist_ok=True)
        nombre_base = f"{nombre_pieza}_ex{indice:02d}"
        (carpeta_pieza / f"{nombre_base}.musicxml").write_bytes(xml_bytes)

        metadatos = {
            'pieza': nombre_pieza,
            'excerpt': nombre_base,
            'compas_inicio': compas_inicio,
            'compas_fin': compas_fin,
            'tipo_corte': tipo_corte,
            'corte_forzado': tipo_corte == 'forzado',
            'bajo_confirma_cadencia': bajo_confirma_cadencia,
            'incluye_anacrusa': incluye_anacrusa,
            **metricas,
        }
        (carpeta_pieza / f"{nombre_base}.json").write_text(
            json.dumps(metadatos, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        csv_writer.writerow(metadatos)

        self._log.log('info', (
            f"{nombre_pieza} ex{indice:02d}: compases {compas_inicio}-{compas_fin} "
            f"({tipo_corte}) -> dificultad={metricas['dificultad_estimada']}"
        ))
        return True

    def _asegurar_atributos_heredados(self, parte_original, parte_excerpt, compas_inicio):
        medidas_excerpt = parte_excerpt.getElementsByClass(music21.stream.Measure)
        if not medidas_excerpt:
            return
        primera_medida = medidas_excerpt[0]

        for clase in (music21.clef.Clef, music21.key.KeySignature, music21.meter.TimeSignature):
            if primera_medida.getElementsByClass(clase):
                continue
            vigente = self._buscar_vigente_antes_de(parte_original, compas_inicio, clase)
            if vigente is not None:
                primera_medida.insert(0, copy.deepcopy(vigente))

    def _buscar_vigente_antes_de(self, parte_original, compas_inicio, clase):
        vigente = None
        for m in parte_original.getElementsByClass(music21.stream.Measure):
            if m.number > compas_inicio:
                break
            encontrados = m.getElementsByClass(clase)
            if encontrados:
                vigente = encontrados[-1]
        return vigente

    # --- métricas (Etapa 2) ---

    def _calcular_metricas(self, excerpt, voz_superior):
        voz_superior_st = voz_superior.stripTies()
        notas = list(voz_superior_st.recurse().notes)
        pitches_todas = [p for n in notas for p in n.pitches]

        if pitches_todas:
            pitch_grave = min(pitches_todas, key=lambda p: p.ps)
            pitch_agudo = max(pitches_todas, key=lambda p: p.ps)
            nota_mas_grave = pitch_grave.nameWithOctave
            nota_mas_aguda = pitch_agudo.nameWithOctave
            ambito_semitonos = int(round(pitch_agudo.ps - pitch_grave.ps))
        else:
            nota_mas_grave = nota_mas_aguda = None
            ambito_semitonos = 0

        linea_melodica = [max(n.pitches, key=lambda p: p.ps) for n in notas]
        intervalos = [
            abs(linea_melodica[i + 1].ps - linea_melodica[i].ps)
            for i in range(len(linea_melodica) - 1)
        ]
        grados_conjuntos = sum(1 for i in intervalos if i < SEMITONOS_SALTO)
        saltos = sum(1 for i in intervalos if i >= SEMITONOS_SALTO)
        salto_maximo = int(round(max(intervalos))) if intervalos else 0
        proporcion_saltos = round(saltos / len(intervalos), 3) if intervalos else 0.0

        duraciones = {round(n.duration.quarterLength, 4) for n in notas}
        variedad_ritmica = len(duraciones)

        sincopas = 0
        for n in notas:
            try:
                fuerza = n.beatStrength
            except Exception:
                continue
            if fuerza is not None and fuerza <= BEAT_STRENGTH_SINCOPA and n.duration.quarterLength >= 1.0:
                sincopas += 1

        ks_encontradas = voz_superior_st.flatten().getElementsByClass(music21.key.KeySignature)
        ks = ks_encontradas[0] if ks_encontradas else music21.key.KeySignature(0)
        accidentales_fuera = 0
        for p in pitches_todas:
            esperado = ks.accidentalByStep(p.step)
            alter_esperado = esperado.alter if esperado else 0.0
            alter_real = p.accidental.alter if p.accidental else 0.0
            if alter_real != alter_esperado:
                accidentales_fuera += 1

        try:
            key_detectada = excerpt.analyze('key')
            tonica = key_detectada.tonic.name
            modo = key_detectada.mode
            confianza = round(key_detectada.correlationCoefficient, 3)
        except Exception:
            tonica = modo = confianza = None

        medidas_excerpt = voz_superior_st.getElementsByClass(music21.stream.Measure)
        cantidad_compases = len(medidas_excerpt)
        compases_ts = voz_superior_st.flatten().getElementsByClass(music21.meter.TimeSignature)
        compas_metrica = compases_ts[0].ratioString if compases_ts else None
        cambios_compas = ';'.join(ts.ratioString for ts in compases_ts[1:]) if len(compases_ts) > 1 else ''

        def puntos(valor, umbrales):
            facil, media = umbrales
            if valor <= facil:
                return 0
            if valor <= media:
                return 1
            return 2

        puntaje = (
            puntos(ambito_semitonos, UMBRALES_DIFICULTAD['ambito_semitonos'])
            + puntos(salto_maximo, UMBRALES_DIFICULTAD['salto_maximo_semitonos'])
            + puntos(proporcion_saltos, UMBRALES_DIFICULTAD['proporcion_saltos'])
            + puntos(accidentales_fuera, UMBRALES_DIFICULTAD['accidentales_fuera_armadura'])
            + puntos(variedad_ritmica, UMBRALES_DIFICULTAD['variedad_ritmica'])
        )
        dificultad_estimada = 'dificil'
        for techo, etiqueta in PUNTAJE_A_ETIQUETA:
            if puntaje <= techo:
                dificultad_estimada = etiqueta
                break

        return {
            'compases': cantidad_compases,
            'nota_mas_grave': nota_mas_grave,
            'nota_mas_aguda': nota_mas_aguda,
            'ambito_semitonos': ambito_semitonos,
            'grados_conjuntos': grados_conjuntos,
            'saltos': saltos,
            'salto_maximo_semitonos': salto_maximo,
            'proporcion_saltos': proporcion_saltos,
            'accidentales_fuera_armadura': accidentales_fuera,
            'variedad_ritmica': variedad_ritmica,
            'sincopas_aprox': sincopas,
            'tonica': tonica,
            'modo': modo,
            'confianza_tonalidad': confianza,
            'compas_metrica': compas_metrica,
            'cambios_compas': cambios_compas,
            'dificultad_estimada': dificultad_estimada,
            'puntaje_dificultad': puntaje,
        }

    # --- verificación por reparseo (mismo espíritu que el generador orquestal) ---

    def _verificar_reparseo(self, xml_bytes, compas_inicio, compas_fin, nombre_pieza, indice):
        esperado = compas_fin - compas_inicio + 1
        try:
            reparseado = music21.converter.parseData(xml_bytes, format='musicxml')
        except Exception as e:
            self._log.log('warning', f"{nombre_pieza} ex{indice:02d}: falló el reparseo de verificación ({e}) -- se descarta.")
            return False

        if not reparseado.parts:
            self._log.log('warning', f"{nombre_pieza} ex{indice:02d}: el reparseo no tiene partes -- se descarta.")
            return False

        primera_parte = reparseado.parts[0]
        medidas = primera_parte.getElementsByClass(music21.stream.Measure)
        if len(medidas) != esperado:
            self._log.log('warning', (
                f"{nombre_pieza} ex{indice:02d}: se esperaban {esperado} compases, el reparseo "
                f"tiene {len(medidas)} -- se descarta."
            ))
            return False

        primera_medida = medidas[0]
        for clase, nombre in (
            (music21.clef.Clef, 'clave'),
            (music21.key.KeySignature, 'armadura'),
            (music21.meter.TimeSignature, 'compás'),
        ):
            if not primera_medida.getElementsByClass(clase):
                self._log.log('warning', (
                    f"{nombre_pieza} ex{indice:02d}: falta {nombre} explícita en el primer compás "
                    f"del reparseo -- se descarta."
                ))
                return False

        if not list(reparseado.recurse().notes):
            self._log.log('warning', f"{nombre_pieza} ex{indice:02d}: el excerpt reparseado no tiene ninguna nota -- se descarta.")
            return False

        return True
