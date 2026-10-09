import json
from unittest.mock import patch

import music21
from django.contrib.auth.models import User
from django.test import Client, TestCase

from trainer.models import (
    Game, MusicalProject, Playlist, SheetMusic, RANGOS_COMODOS,
    _resolver_instrumento_normalizado, INSTRUMENTO_SIN_ALTURA,
)
from trainer.views import _eventos_ejecucion, _auditar_citas_ejecucion, _a_solfeo
from trainer.metricas_ejecucion import (
    calcular_metricas_de_parte, resolver_instrumento, _cortes_registro, construir_mapa_tiempos,
    compactar_alertas_para_prompt,
)


def _mapa_tiempos_de(part):
    """Atajo para tests de un solo instrumento -- construir_mapa_tiempos()
    funciona igual sobre una Part suelta que sobre un Score (ambos son
    Stream), no hace falta envolverla. Devuelve solo tiempos_por_id,
    descartando tempo_asumido (cada test que lo necesita lo pide aparte)."""
    return construir_mapa_tiempos(part)[0]


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


class CSRFProtectionTests(TestCase):
    """
    Confirma que las 14 vistas a las que se les sacó @csrf_exempt en la auditoría de
    seguridad (ver sesión de la auditoría) realmente exigen el token -- y que, con el
    token puesto (como ya lo manda el frontend real en cada template), la request llega
    a la vista en vez de quedar bloqueada por el middleware.

    No valida la lógica de negocio de cada vista -- varias van a devolver 400/404/409
    con estos datos mínimos/IDs inexistentes a propósito, y eso es correcto: el único
    punto de este test es distinguir "Django bloqueó esto por CSRF antes de llegar al
    código de la vista" (síntoma inconfundible: 403 + HTML, la respuesta de
    CsrfViewMiddleware, nunca un JsonResponse) de "la vista lo procesó y devolvió lo
    suyo" (JsonResponse, sea cual sea el status).

    Las 2 vistas que quedaron con @csrf_exempt a propósito (log_study_session,
    api_update_project_state -- llamadas solo vía navigator.sendBeacon(), que no puede
    mandar headers) no están acá: no hay nada de CSRF que probarles, siguen exentas
    deliberadamente.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='csrf_test_user', password='testpass123')
        cls.game = Game.objects.create(slug='csrf-test-game', name='CSRF Test', description='x')
        cls.sheet = SheetMusic.objects.create(title='CSRF Test Sheet')
        cls.project = MusicalProject.objects.create(user=cls.user, sheet_music=cls.sheet)
        cls.playlist = Playlist.objects.create(user=cls.user, name='CSRF Test Playlist')

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username='csrf_test_user', password='testpass123')
        # secure=True en TODAS las requests de esta clase: con SECURE_SSL_REDIRECT=True
        # (production_settings, activo en test porque DEBUG es False acá también), una
        # request no marcada como segura rebota con un 301 antes de llegar a la vista
        # -- simula lo que realmente pasa en producción detrás del proxy de
        # PythonAnywiere (ver SECURE_PROXY_SSL_HEADER en settings.py).
        #
        # El token de CSRF se rota al iniciar sesión -- pedirlo ANTES del login daría
        # un token ya inválido para esta sesión. /biblioteca/ ya renderiza
        # {{ csrf_token }} (biblioteca_list.html), así que esta request deja la cookie
        # csrftoken puesta y vigente para el resto del test.
        self.client.get('/biblioteca/', secure=True)
        self.token = self.client.cookies['csrftoken'].value

    def _endpoints(self):
        from django.urls import reverse
        return [
            ('toggle_favorite', reverse('toggle_favorite', args=[self.sheet.id]), {}),
            ('add_sheet_marker', reverse('add_sheet_marker', args=[self.sheet.id]), {'measure': 1, 'text': 'nota'}),
            ('add_sheet_note', reverse('add_sheet_note', args=[self.sheet.id]), {'text': 'nota'}),
            ('save_rehearsal_config', reverse('save_rehearsal_config', args=[self.sheet.id]), {}),
            ('log_rehearsal_session', reverse('log_rehearsal_session', args=[self.sheet.id]), {}),
            ('playlist_add_sheet', reverse('playlist_add_sheet'), {'playlist_id': self.playlist.id, 'score_id': self.sheet.id}),
            ('api_create_project', reverse('api_create_project', args=[self.sheet.id]), {}),
            ('api_update_project_section', reverse('api_update_project_section', args=[self.project.id]), {}),
            ('record_attempt', reverse('record_attempt', args=[self.game.slug]), {
                'presented_question': 'x', 'guessed_answer': 'y', 'is_correct': True, 'response_time_ms': 100,
            }),
            ('api_log_midi_game', reverse('api_log_midi_game'), {}),
            # IDs inexistentes a propósito -- alcanza para pasar el CSRF y llegar a la
            # vista, que va a responder 404/409 por su cuenta (ver docstring).
            ('orquestador_analizar_confirmado', reverse('orquestador_analizar_confirmado', args=[999999]), {}),
            ('orquestador_generar_link', reverse('orquestador_generar_link', args=[999999]), {}),
            ('orquestador_revocar_link', reverse('orquestador_revocar_link', args=[999999]), {}),
            ('orquestacion_ejercicio_generar', reverse('orquestacion_ejercicio_generar', args=[999999]), {'asignaciones': {}}),
        ]

    def _es_rechazo_csrf(self, response):
        return response.status_code == 403 and 'application/json' not in (response.get('Content-Type') or '')

    def test_sin_token_django_rechaza(self):
        for nombre, url, body in self._endpoints():
            with self.subTest(vista=nombre):
                response = self.client.post(
                    url, data=json.dumps(body), content_type='application/json',
                    secure=True, HTTP_REFERER='https://testserver/',
                )
                self.assertTrue(
                    self._es_rechazo_csrf(response),
                    f"{nombre}: se esperaba que Django lo bloqueara por CSRF (403 + HTML) sin el "
                    f"token, pero dio status={response.status_code} Content-Type={response.get('Content-Type')!r} "
                    f"-- si ya no tiene @csrf_exempt esto es un regreso real, revisar.",
                )

    def test_con_token_pasa_a_la_vista(self):
        for nombre, url, body in self._endpoints():
            with self.subTest(vista=nombre):
                response = self.client.post(
                    url, data=json.dumps(body), content_type='application/json',
                    HTTP_X_CSRFTOKEN=self.token, secure=True,
                    # Con SECURE_SSL_REDIRECT/HTTPS activo, Django exige ADEMÁS del token
                    # que el Referer esté presente y coincida con el origen -- protección
                    # real de Django para requests seguras (no aplica sobre HTTP plano),
                    # que un navegador real ya manda solo en cualquier fetch() same-origin.
                    # Sin este header, el test client (que no lo manda por su cuenta) daba
                    # "Referer checking failed - no Referer" -- no era un problema del fix,
                    # era el test sin terminar de simular una request real.
                    HTTP_REFERER='https://testserver/',
                )
                self.assertFalse(
                    self._es_rechazo_csrf(response),
                    f"{nombre}: con el token puesto (igual que lo manda el frontend real) "
                    f"igual lo bloqueó el CSRF -- status={response.status_code} "
                    f"Content-Type={response.get('Content-Type')!r}. El fetch() correspondiente "
                    f"se rompería en producción.",
                )


def _parte_de_corcheas(nombre, segundos_totales, bpm=120, pitch='G5', por_compas=8):
    """
    Arma una Part de música21 con corcheas consecutivas al pitch/bpm dado,
    durante segundos_totales segundos reales -- usado por MetricasEjecucionTests
    para no depender de un archivo en disco. 'G5' es el pitch por defecto
    porque cae en el tercio MEDIO del ámbito de Flauta (ni grave ni agudo),
    para no mezclar el multiplicador de registro en los tests que no lo piden.
    Devuelve (part, próximo_número_de_compás_libre).
    """
    p = music21.stream.Part()
    p.partName = nombre
    duracion_corchea_seg = (60 / bpm) * 0.5
    total_corcheas = int(round(segundos_totales / duracion_corchea_seg))
    compas_num = 1
    m = music21.stream.Measure(number=compas_num)
    m.insert(0, music21.tempo.MetronomeMark(number=bpm))
    en_compas = 0
    for _ in range(total_corcheas):
        m.append(music21.note.Note(pitch, quarterLength=0.5))
        en_compas += 1
        if en_compas >= por_compas:
            p.append(m)
            compas_num += 1
            m = music21.stream.Measure(number=compas_num)
            en_compas = 0
    if en_compas > 0:
        p.append(m)
        compas_num += 1
    return p, compas_num


class ResolucionInstrumentoTests(TestCase):
    """
    _resolver_instrumento_normalizado (trainer/models.py) -- nivel 2 de
    resolución de instrumento (nivel 1, por clase real de music21, se prueba
    aparte en MetricasEjecucionTests.test_resolucion_nivel_1_clase_real).
    Cada caso es un choque real que el usuario (músico) pidió explícitamente
    verificar: abreviaturas, plurales, tonalidad transpositora, y los pares
    instrumento específico/genérico que suelen convivir en una misma
    partitura real (fagot/contrafagot, trombón/trombón bajo, etc.) en
    español, inglés e italiano.
    """

    def test_especifico_gana_sobre_generico_en_los_tres_idiomas(self):
        casos = [
            ('Contrabassoon', 'Contrafagot'), ('Contrafagot', 'Contrafagot'), ('Controfagotto', 'Contrafagot'),
            ('Bassoon', 'Fagot'), ('Fagot', 'Fagot'), ('Fagotto', 'Fagot'),
            ('English Horn', 'Corno Inglés'), ('Cor Anglais', 'Corno Inglés'), ('Corno inglés', 'Corno Inglés'), ('Corno inglese', 'Corno Inglés'),
            ('French Horn', 'Corno'), ('Horn', 'Corno'), ('Trompa', 'Corno'),
            ('Bass Clarinet', 'Clarinete Bajo'), ('Clarinete bajo', 'Clarinete Bajo'), ('Clarinetto basso', 'Clarinete Bajo'),
            ('Bass Trombone', 'Trombón Bajo'), ('Trombón bajo', 'Trombón Bajo'), ('Trombone basso', 'Trombón Bajo'),
            ('Tenor Trombone', 'Trombón'), ('Trombón tenor', 'Trombón'), ('Trombone', 'Trombón'),
            ('Tenor Sax', 'Saxo Tenor'), ('Alto Sax', 'Saxo Alto'), ('Soprano Sax', 'Saxo Soprano'), ('Baritone Sax', 'Saxo Barítono'),
            ('Alto Flute', 'Flauta Alto'), ('Flauta alto', 'Flauta Alto'),
            ('Bass Flute', 'Flauta Bajo'), ('Flauta bajo', 'Flauta Bajo'),
            ('Contrabass', 'Contrabajo'), ('Double Bass', 'Contrabajo'), ('Contrabbasso', 'Contrabajo'),
        ]
        for nombre, esperado in casos:
            with self.subTest(nombre=nombre):
                self.assertEqual(_resolver_instrumento_normalizado(nombre), esperado)

    def test_abreviaturas_estandar(self):
        casos = [
            ('Fl. 2', 'Flauta'), ('Picc.', 'Flautín'), ('Ob.', 'Oboe'), ('E.H.', 'Corno Inglés'),
            ('Cl.', 'Clarinete'), ('B.Cl.', 'Clarinete Bajo'), ('Bsn.', 'Fagot'), ('Cbsn.', 'Contrafagot'),
            ('Hn.', 'Corno'), ('Tpt.', 'Trompeta'), ('Tbn.', 'Trombón'), ('B.Tbn.', 'Trombón Bajo'),
            ('Tba.', 'Tuba'), ('Sop.', 'Soprano'), ('Alt.', 'Contralto'), ('Ten.', 'Tenor'), ('Bar.', 'Barítono'),
        ]
        for nombre, esperado in casos:
            with self.subTest(nombre=nombre):
                self.assertEqual(_resolver_instrumento_normalizado(nombre), esperado)

    def test_numerales_y_tonalidad_transpositora_se_ignoran(self):
        casos = [
            ('Violin I', 'Violín'), ('Violins I-II', 'Violín'), ('Flutes I-II', 'Flauta'),
            ('Clarinet in Bb', 'Clarinete'), ('Clarinete en Sib', 'Clarinete'), ('Horn in F', 'Corno'),
        ]
        for nombre, esperado in casos:
            with self.subTest(nombre=nombre):
                self.assertEqual(_resolver_instrumento_normalizado(nombre), esperado)

    def test_plural_ingles_y_espanol(self):
        casos = [('Trumpets', 'Trompeta'), ('Violines', 'Violín'), ('Fagotes', 'Fagot'), ('Oboes', 'Oboe'), ('Trombones', 'Trombón')]
        for nombre, esperado in casos:
            with self.subTest(nombre=nombre):
                self.assertEqual(_resolver_instrumento_normalizado(nombre), esperado)

    def test_terminos_excluidos_no_matchean_por_subcadena(self):
        """'Baritone Horn'/'Euphonium'/'Bombardino' NO tienen entrada en la tabla
        a propósito -- no son la voz de Barítono ni ningún instrumento de
        LIMITES_AIRE, y "baritone" es subcadena de "baritone horn" (si no
        estuvieran excluidos explícitamente, matchearían mal)."""
        for nombre in ('Baritone Horn', 'Euphonium', 'Bombardino', 'Flicorno'):
            with self.subTest(nombre=nombre):
                self.assertIsNone(_resolver_instrumento_normalizado(nombre))

    def test_abreviatura_corta_no_matchea_dentro_de_palabra_no_relacionada(self):
        """Guarda anti-falso-positivo: 'ob' (abreviatura de Oboe) no debe
        matchear dentro de una palabra más larga que la contenga como
        subcadena sin ser su propia palabra completa."""
        self.assertIsNone(_resolver_instrumento_normalizado('Doble'))

    def test_bass_ambiguo_se_desempata_por_presencia_de_letra(self):
        parte_sin_letra = music21.stream.Part()
        m = music21.stream.Measure(number=1)
        m.append(music21.note.Note('C2', quarterLength=4.0))
        parte_sin_letra.append(m)
        self.assertEqual(_resolver_instrumento_normalizado('Bass', parte_sin_letra), 'Contrabajo')

        parte_con_letra = music21.stream.Part()
        m2 = music21.stream.Measure(number=1)
        n = music21.note.Note('C3', quarterLength=4.0)
        n.lyric = 'A-men'
        m2.append(n)
        parte_con_letra.append(m2)
        self.assertEqual(_resolver_instrumento_normalizado('Bass', parte_con_letra), 'Bajo')

        # Sin parte (no se puede chequear letra): cae al lado seguro, Contrabajo.
        self.assertEqual(_resolver_instrumento_normalizado('Bass', None), 'Contrabajo')


class MetricasEjecucionTests(TestCase):
    """
    Los 5 casos de tortura que el usuario (músico) pidió explícitamente antes
    de aprobar el diseño de trainer/metricas_ejecucion.py, más cobertura de
    las otras 3 métricas (densidad, saltos, cruce dinámica×registro) y la
    resolución de instrumento nivel 1 (clase real de music21).
    """

    def test_tramo_critico_sin_pausa(self):
        """Flauta, 30s de corcheas sin pausa a 120bpm -> nivel crítico."""
        p, _ = _parte_de_corcheas('Flute', 30, bpm=120)
        res, _ = calcular_metricas_de_parte(p, 'Flute', {}, _mapa_tiempos_de(p))
        self.assertEqual(res['tramos_aire'][0]['nivel'], 'critico')

    def test_pausa_suficiente_corta_el_tramo(self):
        """La misma flauta, pero con solo 6s de corcheas seguidas de una pausa
        de 2s -> ok (la pausa alcanza para cortar, y 6s por sí solas no llegan
        ni a aviso)."""
        p, ultimo_compas = _parte_de_corcheas('Flute', 6, bpm=120)
        m_pausa = music21.stream.Measure(number=ultimo_compas)
        m_pausa.append(music21.note.Rest(quarterLength=4.0))  # 2s a 120bpm
        p.append(m_pausa)
        res, _ = calcular_metricas_de_parte(p, 'Flute', {}, _mapa_tiempos_de(p))
        self.assertEqual(len(res['tramos_aire']), 1)
        self.assertEqual(res['tramos_aire'][0]['nivel'], 'ok')
        self.assertAlmostEqual(res['tramos_aire'][0]['duracion_segundos'], 6.0, places=2)

    def test_oboe_exige_pausa_de_exhalacion_tras_superar_aviso(self):
        """Oboe: un tramo que ya superó su umbral de aviso (20s), seguido de
        una pausa de solo 1.5s (menor a PAUSA_EXHALACION=3.0s), NO debe
        cortar el tramo -- el oboe necesita más tiempo para exhalar el aire
        sobrante que una respiración normal."""
        p, ultimo_compas = _parte_de_corcheas('Oboe', 22, bpm=120)  # oboe aviso=20s
        m_pausa = music21.stream.Measure(number=ultimo_compas)
        m_pausa.append(music21.note.Rest(quarterLength=3.0))  # 1.5s a 120bpm
        p.append(m_pausa)
        m_resto = music21.stream.Measure(number=ultimo_compas + 1)
        for _ in range(8):
            m_resto.append(music21.note.Note('G5', quarterLength=0.5))
        p.append(m_resto)
        res, _ = calcular_metricas_de_parte(p, 'Oboe', {}, _mapa_tiempos_de(p))
        self.assertEqual(len(res['tramos_aire']), 1, "la pausa de 1.5s no debía cortar el tramo")

    def test_registro_grave_pesa_mas_que_registro_medio(self):
        """Fagot: la misma duración real en el tercio grave del ámbito cómodo
        da una duración ponderada (y por lo tanto un nivel) más alto que la
        misma duración en el tercio medio -- confirma mult_grave."""
        corte_grave, corte_agudo = _cortes_registro(RANGOS_COMODOS['Fagot'])
        pitch_grave = RANGOS_COMODOS['Fagot'][0]  # el mínimo cómodo documentado, de por sí grave
        pitch_medio = music21.pitch.Pitch()
        pitch_medio.ps = (corte_grave + corte_agudo) / 2

        p_grave, _ = _parte_de_corcheas('Bassoon', 10, bpm=120, pitch=pitch_grave)
        p_medio, _ = _parte_de_corcheas('Bassoon', 10, bpm=120, pitch=pitch_medio.nameWithOctave)
        res_grave, _ = calcular_metricas_de_parte(p_grave, 'Bassoon', {}, _mapa_tiempos_de(p_grave))
        res_medio, _ = calcular_metricas_de_parte(p_medio, 'Bassoon', {}, _mapa_tiempos_de(p_medio))

        self.assertGreater(res_grave['tramos_aire'][0]['duracion_ponderada'], res_medio['tramos_aire'][0]['duracion_ponderada'])

    def test_cambio_de_tempo_a_mitad_de_obra_se_refleja_en_segundos(self):
        """4 negras a 120bpm (2s) + 4 negras a 60bpm (4s), sin ninguna pausa
        -> el tramo completo mide 6.0s reales, no 8 negras a un tempo fijo."""
        p = music21.stream.Part()
        p.partName = 'Flute'
        m1 = music21.stream.Measure(number=1)
        m1.insert(0, music21.tempo.MetronomeMark(number=120))
        for _ in range(4):
            m1.append(music21.note.Note('G5', quarterLength=1.0))
        p.append(m1)
        m2 = music21.stream.Measure(number=2)
        m2.insert(0, music21.tempo.MetronomeMark(number=60))
        for _ in range(4):
            m2.append(music21.note.Note('G5', quarterLength=1.0))
        p.append(m2)
        res, _ = calcular_metricas_de_parte(p, 'Flute', {}, _mapa_tiempos_de(p))
        self.assertAlmostEqual(res['tramos_aire'][0]['duracion_segundos'], 6.0, places=2)

    def test_tempo_con_termino_de_texto_sin_numero_explicito(self):
        """'Allegro' sin número de metrónomo explícito resuelve via la tabla
        de términos de music21 (defaultTempoValues) a 132bpm -- confirma que
        no hace falta un MetronomeMark numérico para que los segundos salgan
        bien."""
        p = music21.stream.Part()
        p.partName = 'Flute'
        m = music21.stream.Measure(number=1)
        m.insert(0, music21.tempo.TempoText('Allegro'))
        m.append(music21.note.Note('G5', quarterLength=1.0))
        p.append(m)
        res, _ = calcular_metricas_de_parte(p, 'Flute', {}, _mapa_tiempos_de(p))
        self.assertAlmostEqual(res['tramos_aire'][0]['duracion_segundos'], 60 / 132, places=3)

    def test_salto_melodico_mayor_a_octava_cuenta_exacta_no(self):
        p = music21.stream.Part()
        p.partName = 'Violin'
        m = music21.stream.Measure(number=1)
        m.append(music21.note.Note('C4', quarterLength=1.0))
        m.append(music21.note.Note('C6', quarterLength=1.0))  # 24 semitonos, > octava
        m.append(music21.note.Note('C5', quarterLength=1.0))  # 12 semitonos, EXACTO una octava, no cuenta
        p.append(m)
        res, _ = calcular_metricas_de_parte(p, 'Violin', {}, _mapa_tiempos_de(p))
        self.assertEqual(res['saltos_melodicos']['maximo_semitonos'], 24)
        self.assertEqual(res['saltos_melodicos']['cantidad_mayor_octava'], 1)

    def test_cruce_dinamica_registro_agudo_con_ff(self):
        p = music21.stream.Part()
        p.partName = 'Trumpet'
        m = music21.stream.Measure(number=1)
        m.insert(0, music21.dynamics.Dynamic('ff'))
        m.append(music21.note.Note('C6', quarterLength=1.0))  # agudo para trompeta
        p.append(m)
        res, _ = calcular_metricas_de_parte(p, 'Trumpet', {}, _mapa_tiempos_de(p))
        self.assertEqual(len(res['cruce_dinamica_registro']), 1)
        self.assertEqual(res['cruce_dinamica_registro'][0]['registro'], 'agudo')

    def test_cruce_dinamica_registro_agrupa_compases_contiguos(self):
        """Bug real encontrado al inspeccionar la salida JSON real antes de
        cerrar la fase: un pasaje sostenido de muchas notas en el mismo
        registro/dinámica daba una entrada POR NOTA (48 líneas casi idénticas
        para 2 compases). Tiene que agruparse en un solo rango de compases."""
        p = music21.stream.Part()
        p.partName = 'Oboe'
        m1 = music21.stream.Measure(number=1)
        m1.insert(0, music21.dynamics.Dynamic('ff'))
        for _ in range(16):
            m1.append(music21.note.Note('F6', quarterLength=0.5))  # agudo para oboe
        p.append(m1)
        m2 = music21.stream.Measure(number=2)
        for _ in range(16):
            m2.append(music21.note.Note('G5', quarterLength=0.5))  # sigue agudo, sigue ff
        p.append(m2)
        res, _ = calcular_metricas_de_parte(p, 'Oboe', {}, _mapa_tiempos_de(p))
        self.assertEqual(len(res['cruce_dinamica_registro']), 1, "debía agruparse en un solo rango, no una entrada por nota")
        entrada = res['cruce_dinamica_registro'][0]
        self.assertEqual((entrada['compas_desde'], entrada['compas_hasta']), (1, 2))

    def test_densidad_ritmica_detecta_el_compas_mas_denso(self):
        p = music21.stream.Part()
        p.partName = 'Flute'
        m1 = music21.stream.Measure(number=1)
        m1.insert(0, music21.tempo.MetronomeMark(number=120))
        for _ in range(8):
            m1.append(music21.note.Note('G5', quarterLength=0.5))
        p.append(m1)
        m2 = music21.stream.Measure(number=2)
        m2.append(music21.note.Note('G5', quarterLength=4.0))
        p.append(m2)
        res, _ = calcular_metricas_de_parte(p, 'Flute', {}, _mapa_tiempos_de(p))
        self.assertEqual(res['densidad_ritmica'][0]['compas'], 1)

    def test_instrumento_no_reconocido_genera_aviso_explicito(self):
        p = music21.stream.Part()
        p.partName = 'Theremin Cósmico'
        m = music21.stream.Measure(number=1)
        m.append(music21.note.Note('C4', quarterLength=1.0))
        p.append(m)
        avisos = {}
        calcular_metricas_de_parte(p, 'Theremin Cósmico', avisos, _mapa_tiempos_de(p))
        self.assertTrue(any(v['tipo'] == 'instrumento_no_reconocido' for v in avisos.values()))

    def test_dinamica_no_reconocida_se_agrupa_no_una_por_nota(self):
        p = music21.stream.Part()
        p.partName = 'Oboe'
        m = music21.stream.Measure(number=1)
        m.insert(0, music21.dynamics.Dynamic('sfz'))
        for _ in range(3):
            m.append(music21.note.Note('G5', quarterLength=1.0))
        p.append(m)
        avisos = {}
        _, hubo_dynamic = calcular_metricas_de_parte(p, 'Oboe', avisos, _mapa_tiempos_de(p))
        self.assertTrue(hubo_dynamic)
        entradas_sfz = [v for v in avisos.values() if v['tipo'] == 'dinamica_no_reconocida']
        self.assertEqual(len(entradas_sfz), 1, "una marca no reconocida repetida debe agruparse, no una entrada por nota")
        self.assertEqual(entradas_sfz[0]['valor'], 'sfz')
        self.assertEqual(entradas_sfz[0]['ocurrencias'], 1)

    def test_resolucion_nivel_1_clase_real(self):
        """Con un objeto Instrument real de music21 insertado (independiente
        de partName), la resolución tiene que ganar por clase, no por
        nombre."""
        p = music21.stream.Part()
        p.insert(0, music21.instrument.Oboe())
        m = music21.stream.Measure(number=1)
        m.append(music21.note.Note('C4', quarterLength=1.0))
        p.append(m)
        canonico, nivel = resolver_instrumento(p, None)
        self.assertEqual(canonico, 'Oboe')
        self.assertEqual(nivel, 1)

    def test_clarinete_en_sib_queda_en_tono_escrito_no_sonoro(self):
        """Candado de regresión: RANGOS_COMODOS y las métricas nuevas asumen
        tono ESCRITO (igual que estadisticas_por_instrumento en views.py,
        nunca transpuesto a sonoro) -- confirmado reparseando por el mismo
        camino real que usa el analizador (GeneralObjectExporter -> MusicXML
        -> music21.converter.parseData), no asumido."""
        p = music21.stream.Part()
        clarinete = music21.instrument.Clarinet()
        clarinete.transposition = music21.interval.Interval('M-2')
        p.insert(0, clarinete)
        m = music21.stream.Measure(number=1)
        m.append(music21.note.Note('C5', quarterLength=4.0))  # escrita C5
        p.append(m)
        s = music21.stream.Score()
        s.insert(0, p)

        exporter = music21.musicxml.m21ToXml.GeneralObjectExporter(s)
        xml_bytes = exporter.parse()
        s2 = music21.converter.parseData(xml_bytes.decode('utf-8'))
        parte_reparseada = s2.parts[0]

        nota = list(parte_reparseada.recurse().notes)[0]
        self.assertEqual(nota.nameWithOctave, 'C5', "la nota debe seguir escrita en C5, no convertida a Bb4 sonora")

        canonico, _ = resolver_instrumento(parte_reparseada, 'Clarinet')
        self.assertEqual(canonico, 'Clarinete')

    def test_tempo_en_una_parte_se_propaga_a_las_demas(self):
        """
        Candado de regresión de un bug real encontrado al auditar esta fase:
        part.flatten().secondsMap de una parte SIN su propia marca de tempo
        NO ve la marca de otra parte del mismo Score (confirmado con un caso
        de prueba aislado: daba 2.0s/120bpm por defecto en vez de
        2.666s/90bpm). En una partitura real el tempo casi siempre se escribe
        una sola vez, no replicado en cada pentagrama -- por eso
        construir_mapa_tiempos() tiene que calcularse sobre el SCORE completo
        una sola vez, nunca por parte."""
        s = music21.stream.Score()
        flauta = music21.stream.Part()
        flauta.partName = 'Flute'
        oboe = music21.stream.Part()
        oboe.partName = 'Oboe'

        m1f = music21.stream.Measure(number=1)
        m1f.insert(0, music21.tempo.MetronomeMark(number=90))  # SOLO en flauta
        m1f.append(music21.note.Note('G5', quarterLength=4.0))
        flauta.append(m1f)

        m1o = music21.stream.Measure(number=1)
        m1o.append(music21.note.Note('C4', quarterLength=4.0))  # oboe sin marca propia
        oboe.append(m1o)

        s.insert(0, flauta)
        s.insert(0, oboe)

        tiempos_por_id, tempo_asumido = construir_mapa_tiempos(s)
        self.assertFalse(tempo_asumido)

        res_oboe, _ = calcular_metricas_de_parte(oboe, 'Oboe', {}, tiempos_por_id)
        # redonda (4 negras) a 90bpm = 2.666s, NO 2.0s (que sería el default de
        # 120bpm si no hubiera visto la marca de la flauta).
        self.assertAlmostEqual(res_oboe['tramos_aire'][0]['duracion_segundos'], 60 / 90 * 4, places=2)

    def test_aviso_tempo_asumido_cuando_no_hay_ningun_metronome_mark(self):
        p = music21.stream.Part()
        p.partName = 'Flute'
        m = music21.stream.Measure(number=1)
        m.append(music21.note.Note('G5', quarterLength=1.0))
        p.append(m)
        _, tempo_asumido = construir_mapa_tiempos(p)
        self.assertTrue(tempo_asumido)

    def test_sin_aviso_tempo_asumido_cuando_hay_metronome_mark_explicito(self):
        p = music21.stream.Part()
        p.partName = 'Flute'
        m = music21.stream.Measure(number=1)
        m.insert(0, music21.tempo.MetronomeMark(number=90))
        m.append(music21.note.Note('G5', quarterLength=1.0))
        p.append(m)
        _, tempo_asumido = construir_mapa_tiempos(p)
        self.assertFalse(tempo_asumido)


class CompactarAlertasParaPromptTests(TestCase):
    """
    FASE 2B: compactar_alertas_para_prompt(alertas_ejecucion) -- la versión
    reducida y acotada que SÍ entra al prompt de Claude (a diferencia de
    alertas_ejecucion completa, que solo va al panel UI/final_data).
    """

    def test_filtra_nivel_ok(self):
        alertas = {'tramos_aire': [
            {'instrumento': 'Oboe', 'compas_desde': 1, 'compas_hasta': 2, 'duracion_ponderada': 5.0, 'umbral_aviso': 20, 'umbral_critico': 35, 'nivel': 'ok'},
        ], 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [], 'avisos': []}
        self.assertEqual(compactar_alertas_para_prompt(alertas), [])

    def test_topa_tiempo_aire_por_instrumento(self):
        tramos = [
            {'instrumento': 'Oboe', 'compas_desde': n, 'compas_hasta': n, 'duracion_ponderada': float(20 + n), 'umbral_aviso': 20, 'umbral_critico': 35, 'nivel': 'aviso'}
            for n in range(1, 6)  # 5 tramos, tope es 3
        ]
        alertas = {'tramos_aire': tramos, 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [], 'avisos': []}
        compacto = compactar_alertas_para_prompt(alertas)
        self.assertEqual(len(compacto), 3)
        # se quedan los 3 de mayor duracion_ponderada (25, 24, 23), no los primeros 3 en orden de aparicion.
        self.assertEqual([c['duracion_ponderada_seg'] for c in compacto], [25.0, 24.0, 23.0])

    def test_topa_global_y_agrega_alertas_omitidas(self):
        tramos = [
            {'instrumento': f'Instrumento{i}', 'compas_desde': 1, 'compas_hasta': 1, 'duracion_ponderada': float(i), 'umbral_aviso': 5, 'umbral_critico': 100, 'nivel': 'aviso'}
            for i in range(50)  # 50 instrumentos distintos, 1 alerta cada uno -- el tope por instrumento no actua, solo el global (40)
        ]
        alertas = {'tramos_aire': tramos, 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [], 'avisos': []}
        compacto = compactar_alertas_para_prompt(alertas)
        self.assertEqual(len(compacto), 41)  # 40 + la entrada de alertas_omitidas
        self.assertEqual(compacto[-1]['tipo'], 'alertas_omitidas')
        self.assertEqual(compacto[-1]['cantidad'], 10)

    def test_densidad_usa_umbral_no_tope(self):
        """Un pico por debajo de UMBRAL_DENSIDAD_NOTABLE no debe aparecer en
        el compacto, aunque sea 'el mas denso' de ese instrumento -- a
        diferencia del panel UI (TOP_N_PICOS_DENSIDAD), que siempre muestra
        los N mas densos exista o no algo realmente notable."""
        alertas = {'tramos_aire': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [],
                   'densidad_ritmica': [{'instrumento': 'Flauta', 'picos': [{'compas': 1, 'notas_por_segundo': 1.0}]}],
                   'avisos': []}
        self.assertEqual(compactar_alertas_para_prompt(alertas), [])

    def test_obra_sin_ninguna_alerta_da_lista_vacia(self):
        alertas = {'tramos_aire': [], 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [], 'avisos': []}
        self.assertEqual(compactar_alertas_para_prompt(alertas), [])

    def test_incluye_aviso_instrumento_no_reconocido(self):
        alertas = {'tramos_aire': [], 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [],
                   'avisos': [{'tipo': 'instrumento_no_reconocido', 'instrumento': 'Theremin', 'valor': None, 'ocurrencias': 1}]}
        compacto = compactar_alertas_para_prompt(alertas)
        self.assertEqual(len(compacto), 1)
        self.assertEqual(compacto[0]['tipo'], 'instrumento_no_reconocido')

    def test_avisos_nunca_se_recortan_primero(self):
        """Los avisos epistémicos (instrumento_no_reconocido, etc.) cambian
        cómo se interpreta todo lo demás -- no pueden ser lo primero que se
        pierde si hay que aplicar el tope global."""
        tramos = [
            {'instrumento': f'I{i}', 'compas_desde': 1, 'compas_hasta': 1, 'duracion_ponderada': 100.0, 'umbral_aviso': 5, 'umbral_critico': 10, 'nivel': 'critico'}
            for i in range(45)
        ]
        alertas = {'tramos_aire': tramos, 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [],
                   'avisos': [{'tipo': 'instrumento_no_reconocido', 'instrumento': 'Theremin', 'valor': None, 'ocurrencias': 1}]}
        compacto = compactar_alertas_para_prompt(alertas)
        self.assertIn('instrumento_no_reconocido', [c.get('tipo') for c in compacto])


class AuditoriaCitasEjecucionTests(TestCase):
    """FASE 2B: _auditar_citas_ejecucion -- bilingüe (ES/EN), análoga a
    _auditar_citas_duplicaciones pero sin una sola palabra mágica como
    'verificado' (son 4 tópicos con vocabulario propio cada uno)."""

    def setUp(self):
        self.alertas_compactas = [
            {'tipo': 'tiempo_aire', 'instrumento': 'Oboe', 'compas_desde': 10, 'compas_hasta': 20,
             'duracion_ponderada_seg': 25.0, 'umbral_seg': 20, 'nivel': 'aviso'},
        ]

    def _bloque(self, **kwargs):
        base = {
            'rango_compases': '1-20', 'analisis_cuerdas': '', 'analisis_maderas': '',
            'analisis_metales_percusion': '', 'analisis_balance_y_fango': '', 'solucion_prosa': '',
            'ediciones_sugeridas': [], 'alertas_ejecucion_citadas': [],
        }
        base.update(kwargs)
        return base

    def test_mencion_en_espanol_sin_cita_dispara_warning(self):
        bloque = self._bloque(analisis_maderas='El oboe sostiene el pasaje sin pausa, exigiendo mucho aire.')
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        self.assertTrue(any('alertas_ejecucion_citadas' in m for m in cm.output))

    def test_mencion_en_ingles_sin_cita_dispara_warning(self):
        bloque = self._bloque(analisis_maderas='The oboe sustains the passage without a break, demanding a lot of breath.')
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        self.assertTrue(any('alertas_ejecucion_citadas' in m for m in cm.output))

    def test_cita_valida_no_dispara_nada(self):
        bloque = self._bloque(
            analisis_maderas='El oboe sostiene el pasaje sin pausa.',
            alertas_ejecucion_citadas=[{'instrumento': 'Oboe', 'compas_desde': 10, 'compas_hasta': 20, 'tipo': 'tiempo_aire'}],
        )
        with patch('trainer.views.logger') as logger_mock:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        logger_mock.warning.assert_not_called()

    def test_cita_inventada_dispara_warning(self):
        bloque = self._bloque(
            alertas_ejecucion_citadas=[{'instrumento': 'Flauta', 'compas_desde': 1, 'compas_hasta': 2, 'tipo': 'tiempo_aire'}],
        )
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        self.assertTrue(any('inventada' in m for m in cm.output))

    def test_mencion_de_registro_sin_dinamica_extrema_no_dispara(self):
        """Comentar que un instrumento toca en su registro agudo/grave es
        orquestación normal -- solo cuenta como afirmación de
        cruce_dinamica_registro si TAMBIÉN hay una dinámica extrema (ff/fff/
        pp/ppp) mencionada en la misma cláusula."""
        bloque = self._bloque(analisis_maderas='El oboe toca en su registro agudo durante este pasaje.')
        with patch('trainer.views.logger') as logger_mock:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        logger_mock.warning.assert_not_called()


class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=200):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeToolUseBlock:
    type = 'tool_use'

    def __init__(self, input_dict):
        self.input = input_dict


class _FakeEvent:
    def __init__(self, tipo, partial_json=''):
        self.type = tipo
        self.partial_json = partial_json


class _FakeMessage:
    def __init__(self, stop_reason, tool_input):
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()
        self.content = [_FakeToolUseBlock(tool_input)]


class _FakeStream:
    def __init__(self, stop_reason, tool_input):
        self._json_texto = json.dumps(tool_input)
        self._mensaje_final = _FakeMessage(stop_reason, tool_input)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        yield _FakeEvent('input_json', self._json_texto)

    def get_final_message(self):
        return self._mensaje_final


def _reporte_minimo_valido():
    return {
        'resumen_general': 'x', 'bloques': [], 'resumen_por_instrumento': [],
    }


class TruncamientoMaxTokensTests(TestCase):
    """
    FASE 2B (commit 3): stop_reason == 'max_tokens' -> final_data['truncado']
    y se cobra CREDITOS_SI_TRUNCADO en vez de creditos_a_cobrar normal.
    End-to-end real a través de _generar_analisis_orquestacion, mockeando
    solo el cliente de Anthropic (anthropic.Anthropic) -- todo lo demás
    (parseo music21, cálculo de métricas, descuento de créditos real) corre
    de verdad.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='truncamiento_test_user', password='x')

    def _crear_analysis(self):
        from trainer.models import ScoreAnalysis, UserProfile
        from django.core.files.base import ContentFile
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.creditos_bonus = 10
        profile.save()

        s = music21.stream.Score()
        p = music21.stream.Part(); p.partName = 'Flute'
        m = music21.stream.Measure(number=1)
        m.insert(0, music21.tempo.MetronomeMark(number=100))
        m.append(music21.note.Note('G5', quarterLength=4.0))
        p.append(m)
        s.insert(0, p)
        xml_bytes = music21.musicxml.m21ToXml.GeneralObjectExporter(s).parse()

        analysis = ScoreAnalysis.objects.create(user=self.user, name='Test truncamiento')
        analysis.score_file.save('test.musicxml', ContentFile(xml_bytes))
        analysis.save()
        return analysis, profile

    def _correr(self, analysis, stop_reason):
        from trainer.views import _generar_analisis_orquestacion
        fake_stream = _FakeStream(stop_reason, _reporte_minimo_valido())
        fake_client = type('FakeClient', (), {'messages': type('FakeMessages', (), {'stream': staticmethod(lambda **kw: fake_stream)})()})()
        with patch.dict('os.environ', {'ANT_TEST_API_KEY_2': 'fake-key-para-test'}):
            with patch('trainer.views.anthropic.Anthropic', return_value=fake_client):
                resultado = None
                for linea in _generar_analisis_orquestacion(analysis, None, creditos_a_cobrar=1, omitir_chequeo_tamano=True):
                    obj = json.loads(linea)
                    if not obj.get('heartbeat'):
                        resultado = obj
        return resultado

    def test_informe_normal_no_truncado_cobra_credito_completo(self):
        analysis, profile = self._crear_analysis()
        resultado = self._correr(analysis, stop_reason='end_turn')
        self.assertEqual(resultado['status'], 'success')
        self.assertFalse(resultado['data']['truncado'])
        analysis.refresh_from_db()
        self.assertEqual(analysis.creditos_cobrados, 1)

    def test_instrumentacion_de_tiempos_queda_persistida(self):
        """Sin cambiar comportamiento -- solo confirma que
        tiempo_generacion_segundos/tokens_por_segundo quedan guardados en
        ScoreAnalysis después de un análisis exitoso, para poder calibrar
        max_tokens/el timeout de 5 minutos de PythonAnywhere con datos
        reales más adelante."""
        analysis, profile = self._crear_analysis()
        self._correr(analysis, stop_reason='end_turn')
        analysis.refresh_from_db()
        self.assertIsNotNone(analysis.tiempo_generacion_segundos)
        self.assertGreaterEqual(analysis.tiempo_generacion_segundos, 0)
        # tokens_por_segundo puede dar None si tiempo_generacion_segundos
        # redondeó a 0 (guarda división por cero) -- con un stream fake que
        # corre en microsegundos esto es normal, no un bug: en producción,
        # con una llamada real de varios segundos, siempre va a ser > 0.
        if analysis.tokens_por_segundo is not None:
            self.assertGreater(analysis.tokens_por_segundo, 0)

    def test_informe_truncado_marca_flag_y_no_cobra(self):
        from trainer.views import CREDITOS_SI_TRUNCADO
        analysis, profile = self._crear_analysis()
        resultado = self._correr(analysis, stop_reason='max_tokens')
        self.assertEqual(resultado['status'], 'success')
        self.assertTrue(resultado['data']['truncado'])
        analysis.refresh_from_db()
        self.assertEqual(analysis.creditos_cobrados, CREDITOS_SI_TRUNCADO)


class MapaRegistrosPdfLocaleTests(TestCase):
    """
    Bug real de producción: _calcular_mapa_registros/_preparar_densidad_pdf
    devolvían floats crudos, interpolados directo en orquestador_pdf.html
    (width="{{ item.width }}%", rgba(...,{{ item.opacidad }})). Con el
    idioma activo en español, Django localiza el float con COMA decimal
    ("66,13"), y xhtml2pdf hace float("66,13") al parsear ese ancho más
    adelante -> ValueError, PDF export roto en producción. Fix: devolver
    f"{x:.2f}" (string, nunca respeta locale) en vez del float.
    """

    def test_mapa_registros_devuelve_strings_no_floats(self):
        from trainer.views import _calcular_mapa_registros
        estadisticas = {
            'Flauta': {'ambito_min_ps': 72.0, 'ambito_max_ps': 96.0, 'ambito': 'Do5 a Do7'},
            'Oboe': {'ambito_min_ps': 58.0, 'ambito_max_ps': 89.0, 'ambito': 'Sib3 a Fa6'},
            'Fagot': {'ambito_min_ps': 34.0, 'ambito_max_ps': 62.0, 'ambito': 'Sib1 a Re4'},
        }
        mapa = _calcular_mapa_registros(estadisticas)
        for item in mapa:
            for clave in ('left', 'width', 'resto'):
                self.assertIsInstance(item[clave], str, f"{clave} debe ser string, no float")
                float(item[clave])  # nunca debe tirar ValueError por coma decimal

    def test_densidad_pdf_devuelve_strings_no_floats(self):
        from trainer.views import _preparar_densidad_pdf
        densidad = _preparar_densidad_pdf([{'compas': 1, 'instrumentos_activos': 2, 'total_instrumentos': 3}])
        self.assertIsInstance(densidad[0]['opacidad'], str)
        float(densidad[0]['opacidad'])

    def test_locale_espanol_localiza_float_crudo_con_coma(self):
        """Candado de regresión del bug en sí -- confirma que el problema de
        fondo (Django localiza floats con coma en español) sigue siendo
        real, para que el fix de arriba no parezca innecesario si alguien
        lo revierte sin entender por qué está."""
        from django.utils import translation
        from django.template import Template, Context
        with translation.override('es'):
            resultado = Template('{{ valor }}').render(Context({'valor': 66.13}))
        self.assertEqual(resultado, '66,13')

    def test_pdf_completo_se_genera_sin_error_con_datos_reales(self):
        """End-to-end real: el caso exacto que rompía en producción
        (stop_reason del traceback: ValueError en xhtml2pdf al parsear
        '66,13' como ancho de tabla) -- reproducido y confirmado resuelto."""
        from trainer.views import _calcular_mapa_registros, _preparar_densidad_pdf
        from django.template.loader import render_to_string
        from django.utils import translation
        import datetime
        import xhtml2pdf.document as pisa_doc
        import io

        class FakeAnalysis:
            name = 'Test PDF'
            created_at = datetime.datetime.now()

        estadisticas = {
            'Flauta': {'ambito_min_ps': 72.0, 'ambito_max_ps': 96.0, 'ambito': 'Do5 a Do7'},
            'Oboe': {'ambito_min_ps': 58.0, 'ambito_max_ps': 89.0, 'ambito': 'Sib3 a Fa6'},
            'Fagot': {'ambito_min_ps': 34.0, 'ambito_max_ps': 62.0, 'ambito': 'Sib1 a Re4'},
        }
        with translation.override('es'):
            mapa = _calcular_mapa_registros(estadisticas)
            densidad = _preparar_densidad_pdf([{'compas': 1, 'instrumentos_activos': 2, 'total_instrumentos': 3}])
            data = {'resumen_general': 'x', 'resumen_por_instrumento': [], 'estadisticas_por_instrumento': estadisticas}
            html = render_to_string('trainer/orquestador_pdf.html', {
                'analysis': FakeAnalysis(), 'data': data, 'mapa_registros': mapa, 'densidad_pdf': densidad,
            })
            buf = io.BytesIO()
            status = pisa_doc.pisaDocument(html, dest=buf)
        self.assertEqual(status.err, 0)
        self.assertGreater(len(buf.getvalue()), 0)


class AlertasEjecucionEnPdfTests(TestCase):
    """
    Bug real reportado por el usuario: el PDF exportado no incluía
    alertas_ejecucion (tiempo de aire, saltos, cruce dinámica×registro,
    densidad rítmica, avisos) -- orquestador_render.js (panel web) sí la
    mostraba desde FASE 2A/2B, pero orquestador_pdf.html nunca se tocó para
    incluirla. _preparar_alertas_ejecucion_pdf hace el mismo filtrado que
    hace renderAlertasEjecucion del lado del cliente (JS), en Python, porque
    Django templates no filtran listas por valor de un campo.
    """

    def _alertas_de_prueba(self):
        return {
            'tramos_aire': [
                {'instrumento': 'Oboe', 'compas_desde': 10, 'compas_hasta': 20, 'duracion_segundos': 22.0,
                 'duracion_ponderada': 25.3, 'umbral_aviso': 20, 'umbral_critico': 35, 'nivel': 'aviso'},
                {'instrumento': 'Corno Inglés', 'compas_desde': 40, 'compas_hasta': 48, 'duracion_segundos': 38.0,
                 'duracion_ponderada': 40.1, 'umbral_aviso': 18, 'umbral_critico': 30, 'nivel': 'critico'},
                {'instrumento': 'Flauta', 'compas_desde': 1, 'compas_hasta': 2, 'duracion_segundos': 3.0,
                 'duracion_ponderada': 3.0, 'umbral_aviso': 8, 'umbral_critico': 14, 'nivel': 'ok'},
            ],
            'saltos_melodicos': [
                {'instrumento': 'Violonchelo', 'maximo_semitonos': 19.0, 'cantidad_mayor_octava': 3, 'mas_grandes': []},
                {'instrumento': 'Viola', 'maximo_semitonos': 5.0, 'cantidad_mayor_octava': 0, 'mas_grandes': []},
            ],
            'cruce_dinamica_registro': [
                {'instrumento': 'Trompeta', 'compas_desde': 112, 'compas_hasta': 116, 'registro': 'agudo', 'dinamica': 'ff'},
            ],
            'densidad_ritmica': [
                {'instrumento': 'Flauta', 'picos': [{'compas': 30, 'notas_por_segundo': 6.2}, {'compas': 31, 'notas_por_segundo': 4.0}]},
            ],
            'avisos': [
                {'tipo': 'instrumento_no_reconocido', 'instrumento': 'Theremin', 'valor': None, 'ocurrencias': 1},
                {'tipo': 'tempo_asumido', 'instrumento': None, 'valor': 120, 'ocurrencias': 1},
            ],
        }

    def test_filtra_nivel_ok_y_saltos_sin_octava(self):
        from trainer.views import _preparar_alertas_ejecucion_pdf
        preparado = _preparar_alertas_ejecucion_pdf(self._alertas_de_prueba())
        self.assertEqual(len(preparado['tramos_aire']), 2, "el tramo nivel='ok' no debe aparecer")
        self.assertEqual(len(preparado['saltos_melodicos']), 1, "el salto con 0 octavas no debe aparecer")

    def test_umbral_elige_segun_nivel(self):
        from trainer.views import _preparar_alertas_ejecucion_pdf
        preparado = _preparar_alertas_ejecucion_pdf(self._alertas_de_prueba())
        por_instrumento = {t['instrumento']: t for t in preparado['tramos_aire']}
        self.assertEqual(por_instrumento['Oboe']['umbral'], 20)  # nivel aviso -> umbral_aviso
        self.assertEqual(por_instrumento['Corno Inglés']['umbral'], 30)  # nivel critico -> umbral_critico

    def test_densidad_elige_el_pico_mas_denso(self):
        from trainer.views import _preparar_alertas_ejecucion_pdf
        preparado = _preparar_alertas_ejecucion_pdf(self._alertas_de_prueba())
        self.assertEqual(preparado['densidad_ritmica'][0]['pico']['notas_por_segundo'], 6.2)

    def test_pdf_completo_incluye_las_5_secciones(self):
        """End-to-end real: el caso exacto que faltaba -- el PDF ahora
        incluye tiempo de aire, saltos, cruce dinámica×registro, densidad
        rítmica y avisos, no solo lo que ya estaba (resumen, mapa de
        registros, viabilidad, bloques)."""
        from trainer.views import _calcular_mapa_registros, _preparar_densidad_pdf, _preparar_alertas_ejecucion_pdf
        from django.template.loader import render_to_string
        from django.utils import translation
        import datetime
        import xhtml2pdf.document as pisa_doc
        import io

        class FakeAnalysis:
            name = 'Test'
            created_at = datetime.datetime.now()

        alertas_ejecucion = self._alertas_de_prueba()
        data = {
            'resumen_general': 'x', 'resumen_por_instrumento': [], 'estadisticas_por_instrumento': {},
            'alertas_viabilidad': [], 'alertas_ejecucion': alertas_ejecucion, 'bloques': [],
        }
        with translation.override('es'):
            alertas_pdf = _preparar_alertas_ejecucion_pdf(data.get('alertas_ejecucion'))
            html = render_to_string('trainer/orquestador_pdf.html', {
                'analysis': FakeAnalysis(), 'data': data,
                'mapa_registros': _calcular_mapa_registros(data.get('estadisticas_por_instrumento')),
                'densidad_pdf': _preparar_densidad_pdf(data.get('densidad_por_compas')),
                'alertas_ejecucion_pdf': alertas_pdf,
            })
            buf = io.BytesIO()
            status = pisa_doc.pisaDocument(html, dest=buf)
        self.assertEqual(status.err, 0)

        for esperado in (
            'Tiempo de Aire', 'Oboe: tramo sin pausa', 'Corno Inglés: tramo sin pausa',
            'Saltos Melódicos', 'Violonchelo: 3 salto',
            'Cruce Dinámica', 'Trompeta: compases 112-116',
            'Densidad Rítmica', 'Flauta: pico de densidad en el compás 30',
            'Avisos del Análisis de Ejecución', 'Theremin', 'bpm',
        ):
            self.assertIn(esperado, html, f"falta en el PDF: {esperado!r}")


class PercusionSinAlturaYVozAltoTests(TestCase):
    """
    Punto 3 del reporte de bugs sobre "Flauta exagerada": percusión sin altura
    (caja, bombo, platillos, etc.), campanas tubulares y celesta deben
    reconocerse como instrumentos conocidos (sin disparar el aviso de
    "instrumento no reconocido"), y la voz "Alto" a secas solo se reconoce
    como voz de Contralto con evidencia real (letra propia, u otra voz de
    coro entre las partes hermanas) -- nunca por default.
    """

    def test_percusion_sin_altura_no_dispara_aviso(self):
        for nombre in ('Caja', 'Snare Drum', 'Bombo', 'Platillos', 'Triángulo', 'Pandereta'):
            with self.subTest(nombre=nombre):
                self.assertEqual(_resolver_instrumento_normalizado(nombre), INSTRUMENTO_SIN_ALTURA)

    def test_campanas_tubulares_y_celesta_tienen_rango_real_no_sentinel(self):
        for nombre, esperado in (('Campanas Tubulares', 'Campanas Tubulares'), ('Tubular Bells', 'Campanas Tubulares'),
                                  ('Chimes', 'Campanas Tubulares'), ('Celesta', 'Celesta'), ('Celeste', 'Celesta')):
            with self.subTest(nombre=nombre):
                resuelto = _resolver_instrumento_normalizado(nombre)
                self.assertEqual(resuelto, esperado)
                self.assertIn(resuelto, RANGOS_COMODOS)

    def test_campanas_tubulares_ya_no_se_confunde_con_glockenspiel(self):
        """Bug preexistente encontrado de paso: 'campanas tubulares'/'carillon'
        estaban mal mapeadas a 'Glockenspiel' (instrumento distinto, rango
        distinto) en ALIAS_INSTRUMENTO."""
        self.assertEqual(_resolver_instrumento_normalizado('Carillon'), 'Campanas Tubulares')
        self.assertNotEqual(_resolver_instrumento_normalizado('Campanas Tubulares'), 'Glockenspiel')

    def test_percusion_sin_altura_via_calcular_metricas_de_parte_no_genera_aviso(self):
        parte = music21.stream.Part()
        parte.partName = 'Caja'
        m = music21.stream.Measure(number=1)
        m.append(music21.note.Unpitched())
        parte.append(m)
        avisos = {}
        resultado, _hubo = calcular_metricas_de_parte(parte, 'Caja', avisos, _mapa_tiempos_de(parte))
        self.assertEqual(avisos, {})
        self.assertEqual(resultado['tramos_aire'], [])

    def test_instrumento_real_unpitched_percussion_reconocido_por_clase(self):
        """Nivel 1 (clase real de music21, no solo por nombre): un part con un
        instrument.UnpitchedPercussion real (ej. Woodblock) debe resolverse al
        sentinel aunque el partName no esté en ALIAS_PERCUSION_SIN_ALTURA."""
        parte = music21.stream.Part()
        parte.insert(0, music21.instrument.Woodblock())
        canonico, _confianza = resolver_instrumento(parte, 'Percusión rara sin nombre reconocido')
        self.assertEqual(canonico, INSTRUMENTO_SIN_ALTURA)

    def test_alto_solo_no_se_reconoce_sin_evidencia(self):
        self.assertIsNone(_resolver_instrumento_normalizado('Alto'))

    def test_alto_con_letra_se_reconoce_como_contralto(self):
        parte = music21.stream.Part()
        m = music21.stream.Measure(number=1)
        n = music21.note.Note('G4', quarterLength=4.0)
        n.lyric = 'A-men'
        m.append(n)
        parte.append(m)
        self.assertEqual(_resolver_instrumento_normalizado('Alto', parte), 'Contralto')

    def test_alto_con_hermanas_de_coro_se_reconoce_como_contralto(self):
        self.assertEqual(
            _resolver_instrumento_normalizado('Alto', nombres_hermanos=['Soprano', 'Tenor', 'Bajo']),
            'Contralto',
        )

    def test_alto_sin_letra_ni_hermanas_de_coro_sigue_sin_reconocerse(self):
        """Mismo nombre "Alto", pero las hermanas son instrumentales (ej. una
        sección de Viola/Saxo) -- no hay evidencia de coro, no se adivina."""
        self.assertIsNone(_resolver_instrumento_normalizado('Alto', nombres_hermanos=['Violín', 'Violonchelo']))


class DesambiguarNombresPartesTests(TestCase):
    """
    Punto 3 del reporte de bugs: partes con el mismo nombre (ej. dos partes
    "Trumpet" en el archivo real, mostradas como "Trumpet 1/2") deben
    distinguirse en measures_data/estadisticas_por_instrumento/instruments --
    sin esto, la segunda pisaba silenciosamente los datos de la primera.
    """

    def test_nombres_duplicados_reciben_sufijo_numerico(self):
        from trainer.views import _desambiguar_nombres_partes

        class _Parte:
            def __init__(self, nombre):
                self.partName = nombre

        partes = [_Parte('Trumpet'), _Parte('Trumpet'), _Parte('Oboe')]
        self.assertEqual(_desambiguar_nombres_partes(partes), ['Trumpet 1', 'Trumpet 2', 'Oboe'])

    def test_nombres_unicos_no_se_tocan(self):
        from trainer.views import _desambiguar_nombres_partes

        class _Parte:
            def __init__(self, nombre):
                self.partName = nombre

        partes = [_Parte('Flauta'), _Parte('Oboe')]
        self.assertEqual(_desambiguar_nombres_partes(partes), ['Flauta', 'Oboe'])

    def test_partes_sin_nombre_tambien_se_desambiguan(self):
        from trainer.views import _desambiguar_nombres_partes

        class _Parte:
            def __init__(self, nombre):
                self.partName = nombre

        partes = [_Parte(None), _Parte(None)]
        self.assertEqual(
            _desambiguar_nombres_partes(partes),
            ['Instrumento Desconocido 1', 'Instrumento Desconocido 2'],
        )


class AlteracionesComoSimboloTests(TestCase):
    """Punto 3 del reporte de bugs: los bemoles/sostenidos se muestran con el
    símbolo musical real (♭/♯/𝄫/𝄪), no con el guion/almohadilla crudo de
    music21 ('B-3', 'C##4')."""

    def test_bemol_y_sostenido_simple_en_espanol(self):
        from django.utils import translation
        with translation.override('es'):
            self.assertEqual(_a_solfeo('B-3'), 'Si♭3')
            self.assertEqual(_a_solfeo('C#4'), 'Do♯4')
            self.assertEqual(_a_solfeo('F4'), 'Fa4')

    def test_doble_bemol_y_doble_sostenido_en_espanol(self):
        from django.utils import translation
        with translation.override('es'):
            self.assertEqual(_a_solfeo('B--3'), 'Si\U0001D12B3')
            self.assertEqual(_a_solfeo('C##4'), 'Do\U0001D12A4')

    def test_bemol_y_sostenido_en_ingles(self):
        from django.utils import translation
        with translation.override('en'):
            self.assertEqual(_a_solfeo('B-3'), 'B♭3')
            self.assertEqual(_a_solfeo('C#4'), 'C♯4')
