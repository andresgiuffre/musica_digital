import json

import music21
from django.test import TestCase

from trainer.views import _eventos_ejecucion


def _score_dos_manos(medidas_por_hand, repeticion=None):
    """
    Arma un Score sintético de dos partes (treble/bass) a partir de una lista de
    (pitches_treble, pitches_bass) por compás -- cada elemento suena como negra. Usado por
    varios tests de _eventos_ejecucion en vez de depender de un archivo real en disco.
    `repeticion`, si se pasa, es (compas_inicio, compas_fin) y marca bar.Repeat en AMBAS
    partes en esos compases.
    """
    s = music21.stream.Score()
    treble = music21.stream.Part()
    bass = music21.stream.Part()

    for i, (pt, pb) in enumerate(medidas_por_hand):
        numero = i + 1
        mt = music21.stream.Measure(number=numero)
        for p in pt:
            n = music21.note.Note(p)
            n.duration.quarterLength = 4.0 / len(pt)
            mt.append(n)
        if repeticion and numero == repeticion[0]:
            mt.leftBarline = music21.bar.Repeat(direction='start')
        if repeticion and numero == repeticion[1]:
            mt.rightBarline = music21.bar.Repeat(direction='end', times=2)
        treble.append(mt)

        mb = music21.stream.Measure(number=numero)
        for p in pb:
            n = music21.note.Note(p)
            n.duration.quarterLength = 4.0 / len(pb)
            mb.append(n)
        if repeticion and numero == repeticion[0]:
            mb.leftBarline = music21.bar.Repeat(direction='start')
        if repeticion and numero == repeticion[1]:
            mb.rightBarline = music21.bar.Repeat(direction='end', times=2)
        bass.append(mb)

    s.insert(0, treble)
    s.insert(0, bass)
    return s


class EventosEjecucionTests(TestCase):
    """
    Casos de tortura reales que ya rompieron _eventos_ejecucion en producción -- cada uno
    documentado en el diagnóstico que lo originó. No son exhaustivos de música21 en general,
    son específicamente los que ya mordieron una vez.
    """

    def test_repeticion_asimetrica_entre_manos_no_desalinea(self):
        """
        Caso real: la barra de repetición a veces solo queda marcada en UNA de las dos
        manos en el MusicXML exportado, aunque visualmente atraviese el sistema completo.
        _secuencia_compases_canonica() tiene que usar la mano que sí la tiene marcada como
        referencia para las dos, no promediar/truncar por posición.
        """
        s = music21.stream.Score()
        treble = music21.stream.Part()
        bass = music21.stream.Part()
        pt = ['C5', 'D5', 'E5', 'F5']
        pb = ['C3', 'D3', 'E3', 'F3']
        for i in range(4):
            mt = music21.stream.Measure(number=i + 1)
            n = music21.note.Note(pt[i]); n.duration.quarterLength = 4.0
            mt.append(n)
            if i == 0:
                mt.leftBarline = music21.bar.Repeat(direction='start')
            if i == 1:
                # Solo esta mano tiene la barra de cierre -- la otra no.
                mt.rightBarline = music21.bar.Repeat(direction='end', times=2)
            treble.append(mt)

            mb = music21.stream.Measure(number=i + 1)
            n = music21.note.Note(pb[i]); n.duration.quarterLength = 4.0
            mb.append(n)
            bass.append(mb)
        s.insert(0, treble)
        s.insert(0, bass)

        eventos = _eventos_ejecucion(s)
        secuencia_compases = []
        for e in eventos:
            if not secuencia_compases or secuencia_compases[-1] != e['compas_impreso']:
                secuencia_compases.append(e['compas_impreso'])
        self.assertEqual(secuencia_compases, [1, 2, 1, 2, 3, 4])

        # Cada paso_ejecucion de la repetición tiene que traer AMBAS manos, no una sola.
        por_paso = {}
        for e in eventos:
            por_paso.setdefault(e['paso_ejecucion'], []).append(e['pitch'])
        for pitches in por_paso.values():
            self.assertEqual(len(pitches), 2, f"paso con manos incompletas: {pitches}")

    def test_ligadura_no_reataca(self):
        """Una nota ligada entre dos compases debe sumar duración, no re-atacar."""
        s = music21.stream.Score()
        part = music21.stream.Part()

        m1 = music21.stream.Measure(number=1)
        n1 = music21.note.Note('C5')
        n1.duration.quarterLength = 4.0
        n1.tie = music21.tie.Tie('start')
        m1.append(n1)
        part.append(m1)

        m2 = music21.stream.Measure(number=2)
        n2 = music21.note.Note('C5')
        n2.duration.quarterLength = 2.0
        n2.tie = music21.tie.Tie('stop')
        m2.append(n2)
        rest = music21.note.Rest()
        rest.duration.quarterLength = 2.0
        m2.append(rest)
        part.append(m2)

        s.insert(0, part)

        eventos = _eventos_ejecucion(s)
        ataques = [e for e in eventos if not e['es_ligadura_continuacion']]
        continuaciones = [e for e in eventos if e['es_ligadura_continuacion']]

        self.assertEqual(len(ataques), 1)
        self.assertEqual(len(continuaciones), 1)
        self.assertAlmostEqual(ataques[0]['duracion_ql'], 6.0)

    def test_chord_symbol_no_suena(self):
        """Un cifrado (ChordSymbol, ej. 'F' escrito arriba del pentagrama) no debe
        aparecer en la secuencia de audio -- hereda de Chord en music21 pero no es una
        nota real."""
        s = music21.stream.Score()
        part = music21.stream.Part()
        m = music21.stream.Measure(number=1)
        m.append(music21.harmony.ChordSymbol('F'))  # pitches F3/A3/C4, quarterLength 0
        n = music21.note.Note('A4')
        n.duration.quarterLength = 4.0
        m.append(n)
        part.append(m)
        s.insert(0, part)

        eventos = _eventos_ejecucion(s)
        pitches = [e['pitch'] for e in eventos]
        self.assertEqual(pitches, ['A4'])

    def test_altura_duplicada_dentro_del_mismo_acorde_se_descarta(self):
        """
        Caso real de producción: un Chord de music21 puede traer la MISMA altura escrita
        dos veces (doblado de octava/unísono dentro de un mismo acorde -- notación real de
        piano). music21 no lo deduplica solo (confirmado: Chord(['C4','E4','G4','C4']).pitches
        trae las dos C4). Sin filtrar esto, el frontend terminaba disparando la misma nota
        dos veces en el mismo instante exacto -- Tone.js tira "Start time must be strictly
        greater than previous start time" en el segundo trigger, matando ese trigger (la
        nota no suena ni se ilumina).
        """
        s = music21.stream.Score()
        part = music21.stream.Part()
        m = music21.stream.Measure(number=1)
        c = music21.chord.Chord(['C4', 'E4', 'G4', 'C4'])
        c.duration.quarterLength = 4.0
        m.append(c)
        part.append(m)
        s.insert(0, part)

        eventos = _eventos_ejecucion(s)

        self.assertEqual(len(eventos), 3)
        pitches = sorted(e['pitch'] for e in eventos)
        self.assertEqual(pitches, ['C4', 'E4', 'G4'])

    def test_offset_global_no_se_infla_con_ligadura_simultanea_a_notas_cortas(self):
        """
        Caso real de producción: una mano con una nota larga ligada entre compases,
        sonando en simultáneo con la otra mano atacando notas cortas por encima. El
        frontend solía derivar el avance del timeline (`time`) de
        `max(duración de las notas del paso)` -- con una ligadura ahí en medio, ese máximo
        quedaba dominado por la duración LOCAL del segmento de continuación (que no
        representa ningún ataque nuevo) e inflaba el avance, corriendo todo lo que sigue
        cada vez más adelante del camino impreso (confirmado con debugCompararSecuencias()
        sobre un archivo real, sin ninguna repetición de por medio). offset_global es el
        reemplazo: una posición absoluta calculada acá, en el backend, a partir de la
        duración nominal de cada compás -- este test fija ese contrato.
        """
        s = music21.stream.Score()
        treble = music21.stream.Part()
        bass = music21.stream.Part()

        m1t = music21.stream.Measure(number=1)
        for p in ('C5', 'D5', 'E5', 'F5'):
            n = music21.note.Note(p)
            n.duration.quarterLength = 1.0
            m1t.append(n)
        treble.append(m1t)

        m1b = music21.stream.Measure(number=1)
        n1 = music21.note.Note('C3')
        n1.duration.quarterLength = 1.0
        m1b.append(n1)
        n2 = music21.note.Note('F3')
        n2.duration.quarterLength = 3.0
        n2.tie = music21.tie.Tie('start')
        m1b.append(n2)
        bass.append(m1b)

        m2t = music21.stream.Measure(number=2)
        for p in ('G5', 'A5', 'B5', 'C6'):
            n = music21.note.Note(p)
            n.duration.quarterLength = 1.0
            m2t.append(n)
        treble.append(m2t)

        m2b = music21.stream.Measure(number=2)
        n3 = music21.note.Note('F3')
        n3.duration.quarterLength = 2.0
        n3.tie = music21.tie.Tie('stop')
        m2b.append(n3)
        n4 = music21.note.Note('G3')
        n4.duration.quarterLength = 2.0
        m2b.append(n4)
        bass.append(m2b)

        s.insert(0, treble)
        s.insert(0, bass)

        eventos = _eventos_ejecucion(s)

        offsets = [e['offset_global'] for e in eventos]
        self.assertEqual(offsets, sorted(offsets), "offset_global debe ser monótono no decreciente")

        ataque_f3 = [e for e in eventos if e['pitch'] == 'F3' and not e['es_ligadura_continuacion']]
        self.assertEqual(len(ataque_f3), 1)
        self.assertAlmostEqual(ataque_f3[0]['offset_global'], 1.0)
        self.assertAlmostEqual(ataque_f3[0]['duracion_ql'], 5.0)  # 3.0 + 2.0 acumulado por la ligadura

        offsets_compas_2 = [e['offset_global'] for e in eventos if e['compas_impreso'] == 2]
        self.assertAlmostEqual(min(offsets_compas_2), 4.0)  # el compás 1 dura 4 negras

    def test_tresillo_serializa_a_json_sin_error(self):
        """
        Caso real de producción: un tresillo hace que music21 devuelva quarterLength (y el
        offset interno) como fractions.Fraction. json.dumps no sabe serializar Fraction --
        sin convertir a float explícitamente, el endpoint devolvía un 500 crudo (el
        try/except de la vista solo cubre _eventos_ejecucion, no el JsonResponse
        posterior). Este test pega directo en el punto exacto que rompía: serializar la
        salida de _eventos_ejecucion.
        """
        s = music21.stream.Score()
        part = music21.stream.Part()
        m = music21.stream.Measure(number=1)
        tup = music21.duration.Tuplet(3, 2)
        for pname in ('C5', 'D5', 'E5'):
            n = music21.note.Note(pname)
            n.duration.quarterLength = music21.common.opFrac(1) / 3
            n.duration.appendTuplet(tup)
            m.append(n)
        part.append(m)
        s.insert(0, part)

        eventos = _eventos_ejecucion(s)

        try:
            serializado = json.dumps({'status': 'success', 'eventos': eventos})
        except TypeError as e:
            self.fail(f"La secuencia de tresillos no serializó a JSON: {e}")

        recargado = json.loads(serializado)
        duraciones = [e['duracion_ql'] for e in recargado['eventos']]
        for d in duraciones:
            self.assertIsInstance(d, float)
            self.assertAlmostEqual(d, 1 / 3, places=5)
