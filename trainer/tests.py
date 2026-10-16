import json
from unittest.mock import patch

import music21
from django.contrib.auth.models import User
from django.test import Client, TestCase

from trainer.models import (
    Game, MusicalProject, Playlist, SheetMusic, RANGOS_COMODOS,
    _resolver_instrumento_normalizado, INSTRUMENTO_SIN_ALTURA,
)
from trainer.views import (
    _eventos_ejecucion, _auditar_citas_ejecucion, _a_solfeo,
    _auditar_citas_duplicaciones, comparar_versiones,
    evaluar_viabilidad_instrumental, detectar_duplicaciones_verificadas,
    detectar_alertas_criticas_no_comentadas, _auditar_identificadores_con_guion_bajo,
)
from trainer.metricas_ejecucion import (
    calcular_metricas_de_parte, resolver_instrumento, _cortes_registro, construir_mapa_tiempos,
    compactar_alertas_para_prompt, rango_comodo_sonando, pitch_a_sonando,
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

    def test_salto_melodico_incluye_compas_y_nombre_de_intervalo(self):
        """Punto C del reporte de bugs: cada entrada de mas_grandes tiene que
        llevar compas_desde/compas_hasta (ya existía) y nombre_intervalo
        (nuevo) -- calculado con music21, nunca dejado para que el modelo lo
        nombre."""
        p = music21.stream.Part()
        p.partName = 'Violin'
        m1 = music21.stream.Measure(number=5)
        m1.append(music21.note.Note('C4', quarterLength=1.0))
        m2 = music21.stream.Measure(number=6)
        m2.append(music21.note.Note('D5', quarterLength=1.0))  # novena mayor (14 semitonos)
        p.append(m1)
        p.append(m2)
        res, _ = calcular_metricas_de_parte(p, 'Violin', {}, _mapa_tiempos_de(p))
        mayor = res['saltos_melodicos']['mas_grandes'][0]
        self.assertEqual(mayor['compas_desde'], 5)
        self.assertEqual(mayor['compas_hasta'], 6)
        self.assertEqual(mayor['nombre_intervalo'], 'novena mayor')

    def test_motivo_registro_dinamica_ambos_y_ninguno(self):
        """Punto 1 del pedido de correcciones sobre ponderación: motivo
        refleja de dónde viene el peso del tramo -- registro grave, dinámica
        fuerte, ambos a la vez, o ninguno (tramo en registro/dinámica
        normales, aunque haya durado lo suficiente para dar aviso/crítico)."""
        pitch_grave = RANGOS_COMODOS['Contrafagot'][0]  # mínimo cómodo, de por sí grave
        corte_grave, corte_agudo = _cortes_registro(RANGOS_COMODOS['Contrafagot'])
        pitch_medio = music21.pitch.Pitch()
        pitch_medio.ps = (corte_grave + corte_agudo) / 2

        def _parte(pitch, dinamica=None):
            p = music21.stream.Part()
            p.partName = 'Contrabassoon'
            m = music21.stream.Measure(number=1)
            m.insert(0, music21.tempo.MetronomeMark(number=120))
            if dinamica:
                m.insert(0, music21.dynamics.Dynamic(dinamica))
            for _ in range(20):
                m.append(music21.note.Note(pitch, quarterLength=1.0))
            p.append(m)
            return p

        casos = [
            (pitch_grave, None, 'registro'),
            (pitch_medio.nameWithOctave, 'fff', 'dinamica'),
            (pitch_grave, 'fff', 'ambos'),
            (pitch_medio.nameWithOctave, None, 'ninguno'),
        ]
        for pitch, dinamica, motivo_esperado in casos:
            with self.subTest(pitch=pitch, dinamica=dinamica):
                p = _parte(pitch, dinamica)
                res, _ = calcular_metricas_de_parte(p, 'Contrabassoon', {}, _mapa_tiempos_de(p))
                self.assertEqual(res['tramos_aire'][0]['motivo'], motivo_esperado)

    def test_tope_duracion_ponderada_se_aplica_de_punta_a_punta(self):
        """Punto E del reporte de bugs (aprobado): Contrafagot (mult_grave=0.6)
        en registro grave a fff (mult_din=0.5) da un factor combinado teórico
        de 1/0.3 = 3.33x -- por encima del tope (3.0x) aprobado. La duración
        ponderada final nunca debe superar duracion_real * TOPE_DURACION_PONDERADA."""
        from trainer.metricas_ejecucion import TOPE_DURACION_PONDERADA
        pitch_grave = RANGOS_COMODOS['Contrafagot'][0]
        p = music21.stream.Part()
        p.partName = 'Contrabassoon'
        m = music21.stream.Measure(number=1)
        m.insert(0, music21.tempo.MetronomeMark(number=120))
        m.insert(0, music21.dynamics.Dynamic('fff'))
        for _ in range(20):
            m.append(music21.note.Note(pitch_grave, quarterLength=1.0))
        p.append(m)
        res, _ = calcular_metricas_de_parte(p, 'Contrabassoon', {}, _mapa_tiempos_de(p))
        tramo = res['tramos_aire'][0]
        self.assertAlmostEqual(tramo['duracion_ponderada'], tramo['duracion_segundos'] * TOPE_DURACION_PONDERADA, places=5)
        # Sin tope hubiera dado duracion_segundos / 0.3 (~3.33x) -- confirmamos que es MENOR a eso.
        self.assertLess(tramo['duracion_ponderada'], tramo['duracion_segundos'] / 0.3)

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
        canonico, nivel, _intervalo_real = resolver_instrumento(p, None)
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

        canonico, _nivel, _intervalo_real = resolver_instrumento(parte_reparseada, 'Clarinet')
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


class NombreIntervaloTests(TestCase):
    """Punto C del reporte de bugs: nombre_intervalo(pitch_a, pitch_b)
    calcula el nombre real (calidad + grado, en español) con
    music21.interval.Interval, nunca una tabla propia de nombres."""

    def test_casos_conocidos(self):
        from trainer.metricas_ejecucion import nombre_intervalo
        casos = [
            ('C4', 'C5', 'octava justa'),
            ('C4', 'D5', 'novena mayor'),
            ('C4', 'Db5', 'novena menor'),
            ('C4', 'C#5', 'octava aumentada'),
            ('C4', 'B4', 'séptima mayor'),
            ('C4', 'G5', 'duodécima justa'),
        ]
        for a, b, esperado in casos:
            with self.subTest(a=a, b=b):
                self.assertEqual(nombre_intervalo(music21.pitch.Pitch(a), music21.pitch.Pitch(b)), esperado)

    def test_direccion_no_afecta_el_nombre(self):
        """C5->C4 (descendente) tiene que dar el mismo nombre que C4->C5
        (ascendente) -- confirmado que music21 solo cambia el signo de
        .semitones, nunca la calidad/grado del nombre."""
        from trainer.metricas_ejecucion import nombre_intervalo
        self.assertEqual(
            nombre_intervalo(music21.pitch.Pitch('C5'), music21.pitch.Pitch('C4')),
            nombre_intervalo(music21.pitch.Pitch('C4'), music21.pitch.Pitch('C5')),
        )

    def test_intervalo_compuesto_mas_alla_de_la_octava(self):
        from trainer.metricas_ejecucion import nombre_intervalo
        self.assertEqual(nombre_intervalo(music21.pitch.Pitch('C4'), music21.pitch.Pitch('E5')), 'décima mayor')


class TopeDuracionPonderadaTests(TestCase):
    """
    Punto E del reporte de bugs (diagnóstico, luego aprobado): tope directo
    sobre _fusionar_intervalos_con_demanda -- duracion_ponderada nunca puede
    superar duracion_real * TOPE_DURACION_PONDERADA (provisoria, 3.0).
    """

    def test_factor_por_encima_del_tope_se_recorta(self):
        from trainer.metricas_ejecucion import _fusionar_intervalos_con_demanda, TOPE_DURACION_PONDERADA
        # mult=0.3 -> factor teorico 1/0.3 = 3.33x, por encima del tope (3.0x).
        notas = [{'offset_segundos': 0.0, 'duracion_segundos': 10.0, 'mult': 0.3}]
        duracion_real, duracion_ponderada = _fusionar_intervalos_con_demanda(notas)
        self.assertAlmostEqual(duracion_real, 10.0)
        self.assertAlmostEqual(duracion_ponderada, 10.0 * TOPE_DURACION_PONDERADA)

    def test_factor_por_debajo_del_tope_no_se_toca(self):
        from trainer.metricas_ejecucion import _fusionar_intervalos_con_demanda
        # mult=0.6 -> factor 1/0.6 = 1.667x, bien debajo del tope (3.0x).
        notas = [{'offset_segundos': 0.0, 'duracion_segundos': 10.0, 'mult': 0.6}]
        duracion_real, duracion_ponderada = _fusionar_intervalos_con_demanda(notas)
        self.assertAlmostEqual(duracion_ponderada, 10.0 / 0.6)


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
            {'instrumento': 'Oboe', 'compas_desde': n, 'compas_hasta': n, 'duracion_segundos': float(18 + n),
             'duracion_ponderada': float(20 + n), 'umbral_aviso': 20, 'umbral_critico': 35, 'nivel': 'aviso', 'motivo': 'registro'}
            for n in range(1, 6)  # 5 tramos, tope es 3
        ]
        alertas = {'tramos_aire': tramos, 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [], 'avisos': []}
        compacto = compactar_alertas_para_prompt(alertas)
        self.assertEqual(len(compacto), 3)
        # se quedan los 3 de mayor duracion_ponderada (25, 24, 23), no los primeros 3 en orden de aparicion.
        self.assertEqual([c['duracion_ponderada_seg'] for c in compacto], [25.0, 24.0, 23.0])

    def test_tiempo_aire_incluye_duracion_real_y_motivo(self):
        """Punto 2 del pedido de correcciones sobre ponderación: la entrada
        que recibe el modelo tiene que llevar duracion_real_seg y motivo,
        no solo duracion_ponderada_seg -- el informe describe el tramo con
        la duración REAL, nunca con la ponderada."""
        alertas = {'tramos_aire': [
            {'instrumento': 'Contrabassoon', 'compas_desde': 29, 'compas_hasta': 34, 'duracion_segundos': 10.3,
             'duracion_ponderada': 23.7, 'umbral_aviso': 7, 'umbral_critico': 12, 'nivel': 'critico', 'motivo': 'ambos'},
        ], 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [], 'avisos': []}
        compacto = compactar_alertas_para_prompt(alertas)
        self.assertEqual(len(compacto), 1)
        self.assertEqual(compacto[0]['duracion_real_seg'], 10.3)
        self.assertEqual(compacto[0]['motivo'], 'ambos')
        self.assertEqual(compacto[0]['duracion_ponderada_seg'], 23.7)

    def test_tiempo_aire_sin_motivo_en_datos_viejos_no_rompe(self):
        """Bug real confirmado (scripts/auditar_analisis.py contra el id=26
        real, generado ANTES de que existiera 'motivo'): un tramo_aire
        guardado antes de este campo no lo tiene -- compactar_alertas_para_prompt
        se usa también para re-auditar informes viejos ya guardados, no solo
        en el momento de generación, así que tiene que tolerar la ausencia
        con un default explícito ('desconocido'), nunca un KeyError."""
        alertas = {'tramos_aire': [
            {'instrumento': 'Flute', 'compas_desde': 5, 'compas_hasta': 23, 'duracion_segundos': 30.0,
             'duracion_ponderada': 34.8, 'umbral_aviso': 8, 'umbral_critico': 14, 'nivel': 'critico'},  # sin 'motivo'
        ], 'densidad_ritmica': [], 'saltos_melodicos': [], 'cruce_dinamica_registro': [], 'avisos': []}
        compacto = compactar_alertas_para_prompt(alertas)  # no debe lanzar KeyError
        self.assertEqual(compacto[0]['motivo'], 'desconocido')

    def test_salto_melodico_sin_nombre_intervalo_en_datos_viejos_no_rompe(self):
        """Mismo bug real, segunda aparición (también confirmado contra
        id=26): 'nombre_intervalo' (punto C) tampoco existía antes -- un
        mas_grandes guardado de esa época no lo tiene."""
        alertas = {
            'tramos_aire': [], 'densidad_ritmica': [], 'cruce_dinamica_registro': [], 'avisos': [],
            'saltos_melodicos': [{
                'instrumento': 'Trombone', 'cantidad_mayor_octava': 8, 'maximo_semitonos': 17.0,
                'mas_grandes': [{'semitonos': 17.0, 'compas_desde': 32, 'compas_hasta': 32}],  # sin 'nombre_intervalo'
            }],
        }
        compacto = compactar_alertas_para_prompt(alertas)  # no debe lanzar KeyError
        self.assertEqual(compacto[0]['nombre_intervalo'], 'desconocido')

    def test_topa_global_y_agrega_alertas_omitidas(self):
        tramos = [
            {'instrumento': f'Instrumento{i}', 'compas_desde': 1, 'compas_hasta': 1, 'duracion_segundos': float(i),
             'duracion_ponderada': float(i), 'umbral_aviso': 5, 'umbral_critico': 100, 'nivel': 'aviso', 'motivo': 'ninguno'}
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

    def test_salto_melodico_incluye_nombre_de_intervalo(self):
        """Punto C del reporte de bugs: la entrada compacta de salto_melodico
        que se le manda al modelo tiene que llevar nombre_intervalo (ya
        calculado con music21 en mas_grandes), no solo semitonos crudos."""
        alertas = {
            'tramos_aire': [], 'densidad_ritmica': [], 'cruce_dinamica_registro': [], 'avisos': [],
            'saltos_melodicos': [{
                'instrumento': 'Violín', 'cantidad_mayor_octava': 1, 'maximo_semitonos': 14.0,
                'mas_grandes': [{'semitonos': 14.0, 'compas_desde': 5, 'compas_hasta': 6, 'nombre_intervalo': 'novena mayor'}],
            }],
        }
        compacto = compactar_alertas_para_prompt(alertas)
        self.assertEqual(len(compacto), 1)
        self.assertEqual(compacto[0]['nombre_intervalo'], 'novena mayor')

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
            {'instrumento': f'I{i}', 'compas_desde': 1, 'compas_hasta': 1, 'duracion_segundos': 50.0,
             'duracion_ponderada': 100.0, 'umbral_aviso': 5, 'umbral_critico': 10, 'nivel': 'critico', 'motivo': 'ambos'}
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

    def test_bloque_malformado_string_suelto_no_rompe_la_auditoria(self):
        """Bug real de producción: en respuestas grandes (max_tokens=120000)
        Claude a veces no respeta el schema del tool_use y un elemento de
        `bloques` sale como string suelto en vez de objeto -- 'str' object has
        no attribute 'get' tumbaba TODO el análisis (y el crédito ya cobrado
        se perdía). La auditoría debe saltear ese bloque, nunca propagar la
        excepción."""
        bloque_bueno = self._bloque(analisis_maderas='El oboe sostiene el pasaje sin pausa.')
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_ejecucion(['un bloque mal formado', bloque_bueno], self.alertas_compactas)
        self.assertTrue(any('no es un dict' in m for m in cm.output))

    def test_cita_o_edicion_malformada_dentro_de_un_bloque_valido_no_rompe(self):
        bloque = self._bloque(
            alertas_ejecucion_citadas=['cita mal formada'],
            ediciones_sugeridas=['edicion mal formada'],
        )
        _auditar_citas_ejecucion([bloque], self.alertas_compactas)  # no debe lanzar

    def test_nivel_citado_distinto_del_real_dispara_warning(self):
        """Punto B del reporte de bugs: self.alertas_compactas tiene nivel
        'aviso' real para Oboe 10-20 -- citar nivel 'critico' tiene que
        detectarse como un desvío, no pasar en silencio."""
        bloque = self._bloque(
            alertas_ejecucion_citadas=[
                {'instrumento': 'Oboe', 'compas_desde': 10, 'compas_hasta': 20, 'tipo': 'tiempo_aire', 'nivel': 'critico'},
            ],
        )
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        self.assertTrue(any('nivel real' in m for m in cm.output))

    def test_nivel_citado_correcto_no_dispara_nada(self):
        bloque = self._bloque(
            alertas_ejecucion_citadas=[
                {'instrumento': 'Oboe', 'compas_desde': 10, 'compas_hasta': 20, 'tipo': 'tiempo_aire', 'nivel': 'aviso'},
            ],
        )
        with patch('trainer.views.logger') as logger_mock:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        logger_mock.warning.assert_not_called()

    def test_cita_de_otro_tipo_no_cubre_mencion_de_densidad(self):
        """Punto J del reporte de bugs: caso real confirmado -- un bloque
        mencionaba "densidad rítmica elevada... según lo medido" para
        Piccolo/Flauta/Oboe, pero alertas_ejecucion_citadas solo traía una
        cita de tiempo_aire (de Piccolo, para OTRO rango de compases). El
        chequeo viejo ("¿hay alguna cita?") daba por bueno esto -- tiene que
        detectar específicamente que la mención de densidad no tiene ninguna
        cita de tipo densidad_ritmica."""
        bloque = self._bloque(
            analisis_maderas=(
                'Piccolo, Flauta y Oboe ejecutan un diseño rítmico muy veloz que, '
                'según lo medido, representa una densidad rítmica elevada y exigente en estos compases.'
            ),
            alertas_ejecucion_citadas=[
                {'instrumento': 'Piccolo', 'compas_desde': 17, 'compas_hasta': 23, 'tipo': 'tiempo_aire', 'nivel': 'critico'},
            ],
        )
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        self.assertTrue(any('densidad_ritmica' in m and 'alertas_ejecucion_citadas' in m for m in cm.output))

    def test_nivel_null_en_la_cita_no_dispara_nada(self):
        """nivel: null es válido para los 3 tipos sin nivel en los datos
        (salto_melodico/cruce_dinamica_registro/densidad_ritmica) -- no debe
        generar ninguna advertencia de desvío."""
        bloque = self._bloque(
            alertas_ejecucion_citadas=[
                {'instrumento': 'Oboe', 'compas_desde': 10, 'compas_hasta': 20, 'tipo': 'tiempo_aire', 'nivel': None},
            ],
        )
        with patch('trainer.views.logger') as logger_mock:
            _auditar_citas_ejecucion([bloque], self.alertas_compactas)
        logger_mock.warning.assert_not_called()


class AlertasCriticasNoComentadasTests(TestCase):
    """
    Punto A del reporte de bugs sobre "Flauta exagerada": a diferencia de
    _auditar_citas_ejecucion (que detecta mención sin cita, o cita
    inventada, bloque por bloque), detectar_alertas_criticas_no_comentadas
    hace una pasada de COBERTURA sobre todo el informe -- una alerta crítica
    que ningún bloque citó en absoluto, el caso real confirmado: la alerta
    crítica de Flute (compases 5-23, nivel crítico) no aparecía citada en
    ningún bloque del informe real.
    """

    def _bloque(self, **kwargs):
        base = {'rango_compases': '1-20', 'alertas_ejecucion_citadas': []}
        base.update(kwargs)
        return base

    def test_critica_sin_ninguna_cita_aparece_en_la_lista(self):
        alertas = [
            {'tipo': 'tiempo_aire', 'instrumento': 'Flute', 'compas_desde': 5, 'compas_hasta': 23,
             'duracion_ponderada_seg': 34.8, 'umbral_seg': 14, 'nivel': 'critico'},
        ]
        no_comentadas = detectar_alertas_criticas_no_comentadas([self._bloque()], alertas)
        self.assertEqual(len(no_comentadas), 1)
        self.assertEqual(no_comentadas[0]['instrumento'], 'Flute')

    def test_critica_citada_en_algun_bloque_no_aparece(self):
        alertas = [
            {'tipo': 'tiempo_aire', 'instrumento': 'Flute', 'compas_desde': 5, 'compas_hasta': 23,
             'duracion_ponderada_seg': 34.8, 'umbral_seg': 14, 'nivel': 'critico'},
        ]
        bloque_con_cita = self._bloque(
            rango_compases='5-23',
            alertas_ejecucion_citadas=[{'tipo': 'tiempo_aire', 'instrumento': 'Flute', 'compas_desde': 5, 'compas_hasta': 23}],
        )
        no_comentadas = detectar_alertas_criticas_no_comentadas([bloque_con_cita], alertas)
        self.assertEqual(no_comentadas, [])

    def test_alerta_nivel_aviso_nunca_aparece_aunque_no_se_cite(self):
        """Solo 'critico' es obligatorio citar -- 'aviso' es deseable pero no
        entra en este respaldo (ver GUIA_ESTILO_ORQUESTAL)."""
        alertas = [
            {'tipo': 'tiempo_aire', 'instrumento': 'Bassoon', 'compas_desde': 10, 'compas_hasta': 20,
             'duracion_ponderada_seg': 21.0, 'umbral_seg': 14, 'nivel': 'aviso'},
        ]
        no_comentadas = detectar_alertas_criticas_no_comentadas([self._bloque()], alertas)
        self.assertEqual(no_comentadas, [])

    def test_bloque_malformado_no_rompe(self):
        alertas = [
            {'tipo': 'tiempo_aire', 'instrumento': 'Flute', 'compas_desde': 5, 'compas_hasta': 23,
             'duracion_ponderada_seg': 34.8, 'umbral_seg': 14, 'nivel': 'critico'},
        ]
        no_comentadas = detectar_alertas_criticas_no_comentadas(['bloque mal formado'], alertas)
        self.assertEqual(len(no_comentadas), 1)

    def test_auditoria_loggea_warning_para_critica_sin_cita(self):
        alertas = [
            {'tipo': 'tiempo_aire', 'instrumento': 'Flute', 'compas_desde': 5, 'compas_hasta': 23,
             'duracion_ponderada_seg': 34.8, 'umbral_seg': 14, 'nivel': 'critico'},
        ]
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_ejecucion([self._bloque()], alertas)
        self.assertTrue(any('sin ninguna cita en todo el informe' in m for m in cm.output))


class HigieneTextoIdentificadoresTests(TestCase):
    """
    Punto I del reporte de bugs sobre "Flauta exagerada": identificadores
    internos de los datos (ej. "intervalo_fijo") filtrándose tal cual en la
    prosa del informe, en vez de traducirse a lenguaje musical natural.
    A diferencia de los tópicos ambiguos ya auditados (requieren vocabulario
    heurístico), esto no lo es: prosa real nunca usa snake_case.
    """

    def test_identificador_en_resumen_general_dispara_warning(self):
        final_data = {'resumen_general': 'Las cuerdas duplican en intervalo_fijo durante este pasaje.', 'bloques': [], 'resumen_por_instrumento': []}
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_identificadores_con_guion_bajo(final_data)
        self.assertTrue(any('intervalo_fijo' in m for m in cm.output))

    def test_identificador_en_bloque_dispara_warning(self):
        final_data = {
            'resumen_general': 'x', 'resumen_por_instrumento': [],
            'bloques': [{
                'rango_compases': '1-9', 'analisis_cuerdas': 'Doblaje en intervalo_fijo entre violines.',
                'analisis_maderas': '', 'analisis_metales_percusion': '', 'analisis_balance_y_fango': '',
                'solucion_prosa': '', 'ediciones_sugeridas': [],
            }],
        }
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_identificadores_con_guion_bajo(final_data)
        self.assertTrue(any('intervalo_fijo' in m for m in cm.output))

    def test_identificador_en_ediciones_sugeridas_dispara_warning(self):
        final_data = {
            'resumen_general': 'x', 'resumen_por_instrumento': [],
            'bloques': [{
                'rango_compases': '1-9', 'analisis_cuerdas': '', 'analisis_maderas': '',
                'analisis_metales_percusion': '', 'analisis_balance_y_fango': '', 'solucion_prosa': '',
                'ediciones_sugeridas': [{'detalle': 'Reducir la duplicacion en tiempo_aire del Oboe.'}],
            }],
        }
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_identificadores_con_guion_bajo(final_data)
        self.assertTrue(any('tiempo_aire' in m for m in cm.output))

    def test_identificador_en_resumen_por_instrumento_dispara_warning(self):
        final_data = {
            'resumen_general': 'x', 'bloques': [],
            'resumen_por_instrumento': [{'instrumento': 'Oboe', 'descripcion': 'Sostiene el pasaje en cruce_dinamica_registro.'}],
        }
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_identificadores_con_guion_bajo(final_data)
        self.assertTrue(any('cruce_dinamica_registro' in m for m in cm.output))

    def test_texto_limpio_no_dispara_nada(self):
        final_data = {
            'resumen_general': 'Las cuerdas duplican a un intervalo fijo durante este pasaje.',
            'resumen_por_instrumento': [{'instrumento': 'Oboe', 'descripcion': 'Sostiene el pasaje en registro agudo.'}],
            'bloques': [{
                'rango_compases': '1-9', 'analisis_cuerdas': 'Doblaje normal entre violines.',
                'analisis_maderas': '', 'analisis_metales_percusion': '', 'analisis_balance_y_fango': '',
                'solucion_prosa': '', 'ediciones_sugeridas': [{'detalle': 'Reducir dinámica.'}],
            }],
        }
        with patch('trainer.views.logger') as logger_mock:
            _auditar_identificadores_con_guion_bajo(final_data)
        logger_mock.warning.assert_not_called()

    def test_final_data_no_dict_no_rompe(self):
        _auditar_identificadores_con_guion_bajo('no es un dict')  # no debe lanzar

    def test_bloque_malformado_no_rompe(self):
        final_data = {'resumen_general': 'x', 'resumen_por_instrumento': [], 'bloques': ['bloque mal formado']}
        _auditar_identificadores_con_guion_bajo(final_data)  # no debe lanzar


class BloquesMalformadosTests(TestCase):
    """
    Bug real de producción ('str' object has no attribute 'get', reportado
    sobre ripp76.pythonanywhere.com): un elemento de `bloques` que sale como
    string suelto (en vez de objeto) en una respuesta grande de Claude no
    debe poder tumbar _auditar_citas_duplicaciones ni comparar_versiones,
    igual que ya se exige para _auditar_citas_ejecucion arriba.
    """

    def test_auditar_citas_duplicaciones_con_bloque_malformado_no_rompe(self):
        bloque_bueno = {
            'rango_compases': '1-20', 'analisis_cuerdas': 'Doblaje verificado entre flauta y oboe.',
            'analisis_maderas': '', 'analisis_metales_percusion': '', 'analisis_balance_y_fango': '',
            'solucion_prosa': '', 'ediciones_sugeridas': [],
            'duplicaciones_citadas': [{'parte_a': 'Flauta', 'parte_b': 'Oboe', 'tipo': 'unísono',
                                       'compas_desde': 1, 'compas_hasta': 20}],
        }
        duplicaciones_verificadas = [{'parte_a': 'Flauta', 'parte_b': 'Oboe', 'tipo': 'unísono',
                                       'compas_desde': 1, 'compas_hasta': 20}]
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_duplicaciones(['un bloque mal formado', bloque_bueno], duplicaciones_verificadas)
        self.assertTrue(any('no es un dict' in m for m in cm.output))

    def test_comparar_versiones_con_bloque_o_edicion_malformada_no_rompe(self):
        class FakeAnalysisAnterior:
            analysis_data = {'bloques': [
                'un bloque mal formado',
                {'ediciones_sugeridas': ['una edicion mal formada']},
            ]}
            score_file = None  # _parsear_score_descifrado falla -> cae a parts_anterior={}, ya contemplado

        resultado = comparar_versiones(FakeAnalysisAnterior(), [])
        self.assertEqual(resultado, [])


class BloquesComoStringCuelgaElProcesoTests(TestCase):
    """
    Incidente real de producción, más grave que BloquesMalformadosTests de
    arriba (que cubre un ELEMENTO de la lista `bloques` malformado): acá
    `bloques` ENTERO llegó siendo un string (no una lista en absoluto) --
    `for bloque in bloques or []:` itera un string CARÁCTER POR CARÁCTER, y
    cada "bloque" (una letra suelta) fallaba el isinstance(dict) y generaba
    un logger.warning -- miles de líneas de log por segundo con un string de
    varios miles de caracteres, que colgó el proceso real en producción
    (confirmado por el usuario: "se me colgó el servidor muy mal"). Cada
    test confirma UNA sola advertencia controlada (nunca una por carácter).
    """

    STRING_LARGO = 'x' * 5000  # simula el texto crudo de una respuesta truncada

    def test_auditar_citas_duplicaciones_con_bloques_string_no_explota(self):
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_duplicaciones(self.STRING_LARGO, [])
        self.assertEqual(len(cm.output), 1, "tiene que haber UNA sola advertencia, no una por carácter")
        self.assertIn("'bloques' no es una lista", cm.output[0])

    def test_auditar_citas_ejecucion_con_bloques_string_no_explota(self):
        with self.assertLogs('trainer.views', level='WARNING') as cm:
            _auditar_citas_ejecucion(self.STRING_LARGO, [])
        self.assertEqual(len(cm.output), 1, "tiene que haber UNA sola advertencia, no una por carácter")
        self.assertIn("'bloques' no es una lista", cm.output[0])

    def test_detectar_alertas_criticas_no_comentadas_con_bloques_string_no_rompe(self):
        alertas = [{'tipo': 'tiempo_aire', 'instrumento': 'Flute', 'compas_desde': 5, 'compas_hasta': 23, 'nivel': 'critico'}]
        resultado = detectar_alertas_criticas_no_comentadas(self.STRING_LARGO, alertas)
        self.assertEqual(resultado, alertas)  # tratado como "sin ninguna cita" -- conservador, no silencioso

    def test_auditar_identificadores_con_bloques_string_no_explota(self):
        final_data = {'resumen_general': 'x', 'resumen_por_instrumento': [], 'bloques': self.STRING_LARGO}
        with patch('trainer.views.logger') as logger_mock:
            _auditar_identificadores_con_guion_bajo(final_data)
        # Un solo warning real (el de _bloques_validos), nunca miles.
        self.assertEqual(logger_mock.warning.call_count, 1)

    def test_comparar_versiones_con_bloques_string_no_explota(self):
        class FakeAnalysisAnterior:
            analysis_data = {'bloques': BloquesComoStringCuelgaElProcesoTests.STRING_LARGO}
            score_file = None

        with patch('trainer.views.logger') as logger_mock:
            resultado = comparar_versiones(FakeAnalysisAnterior(), [])
        self.assertEqual(resultado, [])
        self.assertEqual(logger_mock.warning.call_count, 1)

    def test_exportar_pdf_con_bloques_string_no_cuelga(self):
        """Incidente real reportado por el usuario DESPUÉS del fix de las 5
        auditorías: el PDF "se queda dando vueltas eternamente" -- causa
        real distinta pero del mismo incidente: orquestador_pdf.html hacía
        {% for bloque in data.bloques %} SIN ningún chequeo de tipo. Con
        bloques como string de varios miles de caracteres, xhtml2pdf tenía
        que maquetar un <div> casi vacío por cada carácter y nunca
        terminaba -- a diferencia de las auditorías (que solo loggean), acá
        no hay ninguna excepción que lo delate, por eso "cuelga" en vez de
        tirar un error. Test de punta a punta real, vía el cliente de
        Django, con límite de tiempo explícito para que una regresión
        futura falle rápido en vez de colgar la corrida de tests."""
        import time
        from trainer.models import ScoreAnalysis
        user = User.objects.create_user(username='pdf_cuelgue_test_user', password='x')
        analysis = ScoreAnalysis.objects.create(
            user=user, name='Test PDF no cuelga',
            analysis_data={
                'resumen_general': 'x', 'resumen_por_instrumento': [], 'estadisticas_por_instrumento': {},
                'alertas_viabilidad': [], 'alertas_ejecucion': {},
                'bloques': self.STRING_LARGO,  # el bug real: bloques entero como string
            },
        )
        client = Client()
        client.force_login(user)
        inicio = time.time()
        response = client.get(f'/orquestador/exportar/{analysis.id}/')
        transcurrido = time.time() - inicio
        self.assertEqual(response.status_code, 200)
        self.assertLess(transcurrido, 10, "el PDF tiene que generarse en segundos, no colgarse")


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


class AlturasSonorasEnMeasuresDataTests(TestCase):
    """
    Punto H del reporte de bugs sobre "Flauta exagerada": measures_data --lo
    que efectivamente se le manda al prompt-- llevaba element.nameWithOctave
    crudo (ESCRITO), nunca pasado por pitch_a_sonando. Confirmado contra el
    informe real: Trumpet 1 citada con acordes "A5-D6" (escrito) aunque su
    ámbito sonoro real llega a Do6 (Trompeta transpone -2); Horn 1 citada con
    "Sol5, Fa#5, Mi5" escrito aunque suena una quinta justa abajo. End-to-end
    real a través de _generar_analisis_orquestacion, capturando el `prompt`
    real que se le manda a client.messages.stream (mismo patrón que
    TruncamientoMaxTokensTests, mockeando solo el cliente de Anthropic).
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='alturas_sonoras_test_user', password='x')

    def test_measures_data_enviada_al_prompt_usa_alturas_sonoras_no_escritas(self):
        from trainer.models import ScoreAnalysis, UserProfile
        from django.core.files.base import ContentFile
        from trainer.views import _generar_analisis_orquestacion

        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.creditos_bonus = 10
        profile.save()

        s = music21.stream.Score()
        p = music21.stream.Part()
        p.partName = 'Horn'
        m = music21.stream.Measure(number=1)
        m.insert(0, music21.tempo.MetronomeMark(number=100))
        m.append(music21.note.Note('C5', quarterLength=4.0))  # escrito C5 -- Corno transpone -7 (quinta justa) -> sonando F4
        p.append(m)
        s.insert(0, p)
        xml_bytes = music21.musicxml.m21ToXml.GeneralObjectExporter(s).parse()

        analysis = ScoreAnalysis.objects.create(user=self.user, name='Test alturas sonoras')
        analysis.score_file.save('test.musicxml', ContentFile(xml_bytes))
        analysis.save()

        prompts_capturados = []

        def fake_stream(**kw):
            prompts_capturados.append(kw['messages'][0]['content'])
            return _FakeStream('end_turn', _reporte_minimo_valido())

        fake_client = type(
            'FakeClient', (), {'messages': type('FakeMessages', (), {'stream': staticmethod(fake_stream)})()}
        )()
        with patch.dict('os.environ', {'ANT_TEST_API_KEY_2': 'fake-key-para-test'}):
            with patch('trainer.views.anthropic.Anthropic', return_value=fake_client):
                for linea in _generar_analisis_orquestacion(analysis, None, creditos_a_cobrar=1, omitir_chequeo_tamano=True):
                    json.loads(linea)

        self.assertEqual(len(prompts_capturados), 1)
        prefijo = "Analizá la siguiente estructura de datos musicales extraída de la partitura:\n"
        analysis_data = json.loads(prompts_capturados[0][len(prefijo):])
        notas = analysis_data['measures_data']['Horn'][0]['notes']
        self.assertEqual(notas, ['F4 (whole)'], "measures_data tiene que llevar la altura SONANDO (F4), no la escrita (C5)")


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
            'Tiempo de Aire', 'Oboe: 22,0s seguidos', 'Corno Inglés: 38,0s seguidos',
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
        canonico, _confianza, _intervalo_real = resolver_instrumento(parte, 'Percusión rara sin nombre reconocido')
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


class VozBajoVsContrabajoTests(TestCase):
    """
    Punto F del reporte de bugs sobre "Flauta exagerada": confirmado contra el
    archivo real de producción (coro SATB completo, Soprano/Alto/Tenor/Bass,
    SIN ninguna lírica cargada en las notas) que "Bass" resolvía como
    Contrabajo (transpone -12) en vez de la voz de Bajo (no transpone) --
    exactamente la misma ambigüedad que "Alto" ya resolvía con evidencia de
    hermanas de coro, pero que la rama de "Bass"/"Bajo"/"Basso" no usaba,
    dependiendo solo de .lyrics.
    """

    def test_bass_sin_letra_ni_hermanas_de_coro_sigue_siendo_contrabajo(self):
        """Sin evidencia de coro (ni letra, ni hermanas de voz), "Bass" sigue
        resolviendo como Contrabajo -- no se cambia el comportamiento para el
        caso real de una sección de cuerdas sin coro en la obra."""
        self.assertEqual(
            _resolver_instrumento_normalizado('Bass', nombres_hermanos=['Violín', 'Violonchelo']),
            'Contrabajo',
        )

    def test_bass_con_letra_se_reconoce_como_voz_de_bajo(self):
        parte = music21.stream.Part()
        m = music21.stream.Measure(number=1)
        n = music21.note.Note('A2', quarterLength=4.0)
        n.lyric = 'A-men'
        m.append(n)
        parte.append(m)
        self.assertEqual(_resolver_instrumento_normalizado('Bass', parte), 'Bajo')

    def test_bass_con_hermanas_de_coro_se_reconoce_como_voz_de_bajo_sin_letra(self):
        """Caso real exacto del bug: coro SATB sin ninguna lírica cargada --
        la sola presencia de Soprano/Alto/Tenor como hermanas alcanza."""
        self.assertEqual(
            _resolver_instrumento_normalizado('Bass', nombres_hermanos=['Soprano', 'Alto', 'Tenor']),
            'Bajo',
        )

    def test_bass_con_hermanas_de_coro_no_transpone_via_calcular_metricas_de_parte(self):
        """Verificación de punta a punta: con el canónico correcto ('Bajo'),
        una nota escrita A2 tiene que mostrarse como A2 sonando (0 semitonos),
        nunca como A1 (lo que pasaría si, por error, se resolviera a
        Contrabajo, que transpone -12)."""
        from trainer.metricas_ejecucion import pitch_a_sonando
        canonico, _nivel, _intervalo_real = resolver_instrumento(
            music21.stream.Part(), 'Bass', nombres_hermanos=['Soprano', 'Alto', 'Tenor'],
        )
        self.assertEqual(canonico, 'Bajo')
        sonando = pitch_a_sonando(music21.pitch.Pitch('A2'), canonico)
        self.assertEqual(sonando.nameWithOctave, 'A2')


class NormalizacionPercusionDeTecladoTests(TestCase):
    """
    Punto G del reporte de bugs sobre "Flauta exagerada": Glockenspiel,
    Xilófono y Celesta no tenían transposición propia (music21 no las
    modela, igual que Contrafagot/Guitarra ya documentados en punto 2), así
    que se mostraban en escrito aunque el informe dijera "sonido real".
    Confirmado contra un archivo real de producción que SÍ trae el elemento
    <transpose> de MusicXML para estos 3 instrumentos -- el archivo tiene
    que ganar siempre sobre la tabla/override nueva, nunca aplicarse las dos
    a la vez.
    """

    def test_glockenspiel_xilofono_celesta_transponen_por_tabla_sin_info_del_archivo(self):
        casos = (('Glockenspiel', 'G4', 'G6'), ('Xylophone', 'G4', 'G5'), ('Celesta', 'C5', 'C6'))
        for nombre, escrito, sonando_esperado in casos:
            with self.subTest(nombre=nombre):
                p = music21.stream.Part()
                p.partName = nombre
                inst_cls = {'Glockenspiel': music21.instrument.Glockenspiel,
                            'Xylophone': music21.instrument.Xylophone,
                            'Celesta': music21.instrument.Celesta}[nombre]
                p.insert(0, inst_cls())
                m = music21.stream.Measure(number=1)
                m.append(music21.note.Note(escrito, quarterLength=4.0))
                p.append(m)

                canonico, _nivel, intervalo_real = resolver_instrumento(p, nombre)
                self.assertIsNone(intervalo_real, "sin <transpose> en el archivo, no debe haber intervalo real")
                sonando = pitch_a_sonando(music21.pitch.Pitch(escrito), canonico, intervalo_real)
                self.assertEqual(sonando.nameWithOctave, sonando_esperado)

    def test_xilofono_con_transposicion_real_del_archivo_da_el_mismo_sonando_que_sin_ella(self):
        """Condición 3 del pedido: un archivo CON <transpose> propio (ej.
        MuseScore que lo exporta) y uno SIN él tienen que dar el MISMO ámbito
        sonoro -- ni se duplica el corrimiento ni se pierde."""
        from trainer.metricas_ejecucion import rango_comodo_sonando

        # Archivo SIN informacion de transposicion (instrumento recien
        # construido, igual que el caso mas comun en la practica).
        p_sin = music21.stream.Part()
        p_sin.partName = 'Xylophone'
        p_sin.insert(0, music21.instrument.Xylophone())
        m_sin = music21.stream.Measure(number=1)
        m_sin.append(music21.note.Note('G4', quarterLength=4.0))
        p_sin.append(m_sin)

        # Archivo CON <transpose> real -- reparseado por el mismo camino que
        # usa el analizador (GeneralObjectExporter -> MusicXML -> parseData),
        # mismo patron que test_clarinete_en_sib_queda_en_tono_escrito_no_sonoro.
        p_con = music21.stream.Part()
        xil = music21.instrument.Xylophone()
        xil.transposition = music21.interval.Interval('P8')
        p_con.insert(0, xil)
        m_con = music21.stream.Measure(number=1)
        m_con.append(music21.note.Note('G4', quarterLength=4.0))
        p_con.append(m_con)
        s = music21.stream.Score()
        s.insert(0, p_con)
        exporter = music21.musicxml.m21ToXml.GeneralObjectExporter(s)
        xml_bytes = exporter.parse()
        p_con_reparseada = music21.converter.parseData(xml_bytes.decode('utf-8')).parts[0]

        canonico_sin, _n1, intervalo_sin = resolver_instrumento(p_sin, 'Xylophone')
        canonico_con, _n2, intervalo_con = resolver_instrumento(p_con_reparseada, 'Xylophone')

        self.assertIsNone(intervalo_sin)
        self.assertIsNotNone(intervalo_con, "el archivo reparseado debe traer su propio <transpose>")

        sonando_sin = pitch_a_sonando(music21.pitch.Pitch('G4'), canonico_sin, intervalo_sin)
        sonando_con = pitch_a_sonando(music21.pitch.Pitch('G4'), canonico_con, intervalo_con)
        self.assertEqual(sonando_sin.nameWithOctave, sonando_con.nameWithOctave)
        self.assertEqual(sonando_sin.nameWithOctave, 'G5')

        self.assertEqual(
            rango_comodo_sonando(canonico_sin, intervalo_sin),
            rango_comodo_sonando(canonico_con, intervalo_con),
        )


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


class VocesMultiplesTests(TestCase):
    """
    Punto 1 del reporte de bugs sobre "Flauta exagerada" (confirmado contra
    el archivo real: Horn 2 y Trombone, con divisi de 2 voces en los
    compases 29-34, daban EXACTAMENTE el doble del tiempo de aire real --
    19,6s en vez de 9,8s -- y el Trombone, 8 saltos melódicos fantasma entre
    voces de hasta 17 semitonos que nunca sonaron). Reproducido acá con
    partes sintéticas de 2 voces y MISMO ritmo -- deben dar el mismo
    resultado que una sola voz.
    """

    def _parte_una_voz(self, nombre, pitch, compases=6, bpm=60):
        p = music21.stream.Part()
        p.partName = nombre
        for i in range(compases):
            m = music21.stream.Measure(number=i + 1)
            if i == 0:
                m.insert(0, music21.tempo.MetronomeMark(number=bpm))
            for j in range(4):
                m.insert(j * 1.0, music21.note.Note(pitch, quarterLength=1.0))
            p.append(m)
        return p

    def _parte_divisi(self, nombre, pitch_v1, pitch_v2, compases=6, bpm=60, dinamica=None):
        p = music21.stream.Part()
        p.partName = nombre
        for i in range(compases):
            m = music21.stream.Measure(number=i + 1)
            if i == 0:
                m.insert(0, music21.tempo.MetronomeMark(number=bpm))
            v1 = music21.stream.Voice(id='1')
            v2 = music21.stream.Voice(id='2')
            if dinamica and i == 0:
                v1.insert(0, music21.dynamics.Dynamic(dinamica))
                v2.insert(0, music21.dynamics.Dynamic(dinamica))
            for j in range(4):
                v1.insert(j * 1.0, music21.note.Note(pitch_v1, quarterLength=1.0))
                v2.insert(j * 1.0, music21.note.Note(pitch_v2, quarterLength=1.0))
            m.insert(0, v1)
            m.insert(0, v2)
            p.append(m)
        return p

    def test_tiempo_de_aire_union_no_duplica_con_dos_voces_mismo_ritmo(self):
        p_una_voz = self._parte_una_voz('Horn', 'G4')
        p_divisi = self._parte_divisi('Horn', 'G4', 'E4')
        res_una, _ = calcular_metricas_de_parte(p_una_voz, 'Horn', {}, _mapa_tiempos_de(p_una_voz))
        res_divisi, _ = calcular_metricas_de_parte(p_divisi, 'Horn', {}, _mapa_tiempos_de(p_divisi))
        dur_una = res_una['tramos_aire'][0]['duracion_segundos']
        dur_divisi = res_divisi['tramos_aire'][0]['duracion_segundos']
        self.assertAlmostEqual(dur_una, dur_divisi, places=2)
        self.assertAlmostEqual(dur_divisi, 24.0, places=2)  # 6 compases x 4 negras a 60bpm

    def test_no_aparecen_saltos_falsos_entre_voces(self):
        """Voz 1 fija en C3, voz 2 fija en C5 -- si se midiera (como antes del
        fix) un salto FALSO entre el final de una voz y el principio de la
        otra, saldria un salto de 24 semitonos. Cada voz por separado no
        tiene ningun salto real (misma nota repetida)."""
        p = self._parte_divisi('Trombone', 'C3', 'C5')
        res, _ = calcular_metricas_de_parte(p, 'Trombone', {}, _mapa_tiempos_de(p))
        self.assertEqual(res['saltos_melodicos']['cantidad_mayor_octava'], 0)
        self.assertEqual(res['saltos_melodicos']['maximo_semitonos'], 0)

    def test_salto_real_dentro_de_una_voz_sigue_detectandose(self):
        """El fix no debe "apagar" saltos reales: una sola voz que alterna
        grave/agudo cada compás sí debe detectarlos."""
        p = music21.stream.Part()
        p.partName = 'Trombone'
        for i, pitch in enumerate(['C3', 'C5', 'C3', 'C5']):
            m = music21.stream.Measure(number=i + 1)
            if i == 0:
                m.insert(0, music21.tempo.MetronomeMark(number=60))
            m.insert(0, music21.note.Note(pitch, quarterLength=4.0))
            p.append(m)
        res, _ = calcular_metricas_de_parte(p, 'Trombone', {}, _mapa_tiempos_de(p))
        self.assertGreater(res['saltos_melodicos']['cantidad_mayor_octava'], 0)

    def test_salto_no_se_mide_a_traves_de_un_silencio_largo(self):
        p = music21.stream.Part()
        p.partName = 'Horn'
        m1 = music21.stream.Measure(number=1)
        m1.insert(0, music21.tempo.MetronomeMark(number=60))
        m1.insert(0, music21.note.Note('C3', quarterLength=4.0))
        m_silencio = music21.stream.Measure(number=2)
        m_silencio.insert(0, music21.note.Rest(quarterLength=32.0))  # 32s de silencio a 60bpm
        m3 = music21.stream.Measure(number=3)
        m3.insert(0, music21.note.Note('C6', quarterLength=4.0))  # 3 octavas de diferencia
        p.append(m1)
        p.append(m_silencio)
        p.append(m3)
        res, _ = calcular_metricas_de_parte(p, 'Horn', {}, _mapa_tiempos_de(p))
        self.assertEqual(res['saltos_melodicos']['cantidad_mayor_octava'], 0)
        self.assertEqual(res['saltos_melodicos']['maximo_semitonos'], 0)

    def test_densidad_cuenta_momentos_de_ataque_no_notas(self):
        """2 voces atacando SIEMPRE juntas (mismo ritmo) no deben duplicar la
        densidad respecto de una sola voz con el mismo ritmo -- un acorde o
        dos voces que atacan juntas cuentan como UN ataque."""
        p_una_voz = self._parte_una_voz('Horn', 'G4')
        p_divisi = self._parte_divisi('Horn', 'G4', 'E4')
        res_una, _ = calcular_metricas_de_parte(p_una_voz, 'Horn', {}, _mapa_tiempos_de(p_una_voz))
        res_divisi, _ = calcular_metricas_de_parte(p_divisi, 'Horn', {}, _mapa_tiempos_de(p_divisi))
        picos_una = {p['compas']: p['notas_por_segundo'] for p in res_una['densidad_ritmica']}
        picos_divisi = {p['compas']: p['notas_por_segundo'] for p in res_divisi['densidad_ritmica']}
        self.assertEqual(picos_una, picos_divisi)

    def test_cruce_dinamica_registro_se_evalua_por_voz(self):
        """Voz 1 aguda+ff, voz 2 grave+ff, mismo compás -- deben salir DOS
        entradas separadas (una por voz), nunca mezcladas en una sola."""
        p = self._parte_divisi('Trombone', 'C6', 'C2', dinamica='ff')
        res, _ = calcular_metricas_de_parte(p, 'Trombone', {}, _mapa_tiempos_de(p))
        registros = sorted(c['registro'] for c in res['cruce_dinamica_registro'])
        self.assertEqual(registros, ['agudo', 'grave'])


class EspacioDeAlturasSonoroTests(TestCase):
    """
    Punto 2 del reporte de bugs sobre "Flauta exagerada" (confirmado contra el
    archivo real): todas las alturas tienen que compararse en SONIDO REAL, no
    escritas. Antes del fix, un Piccolo escrito en Fa4 (perfectamente cómodo)
    disparaba "excede el registro grave" porque se comparaba contra un rango
    que además estaba mal cargado en sonando, y un Piccolo+Flauta con las
    MISMAS notas escritas se clasificaba como "unísono" cuando en realidad
    suena una octava (el piccolo transpone +12).
    """

    def _parte_simple(self, nombre, pitches, duracion_cada=4.0):
        p = music21.stream.Part()
        p.partName = nombre
        for i, pitch in enumerate(pitches):
            m = music21.stream.Measure(number=i + 1)
            m.insert(0, music21.note.Note(pitch, quarterLength=duracion_cada))
            p.append(m)
        return p

    def test_piccolo_escrito_fa4_no_genera_alerta_de_registro(self):
        p = self._parte_simple('Piccolo', ['F4', 'F4'])
        alertas = evaluar_viabilidad_instrumental('Piccolo', p)
        self.assertEqual(alertas, [])

    def test_piccolo_y_flauta_mismas_notas_escritas_da_octava(self):
        """Mismas alturas ESCRITAS, mismo ritmo -- el piccolo suena +12
        semitonos sobre la flauta, nunca unísono."""
        p_picc = self._parte_simple('Piccolo', ['C5', 'D5', 'C5'])
        p_fl = self._parte_simple('Flauta', ['C5', 'D5', 'C5'])
        duplicaciones = detectar_duplicaciones_verificadas([p_picc, p_fl], ['Piccolo', 'Flauta'])
        self.assertEqual(len(duplicaciones), 1)
        self.assertEqual(duplicaciones[0]['tipo'], 'octava')
        self.assertEqual(duplicaciones[0]['compas_desde'], 1)
        self.assertEqual(duplicaciones[0]['compas_hasta'], 3)

    def test_clarinete_sib_y_trompa_fa_en_sus_rangos_reales(self):
        """El rango cómodo de un transpositor tiene que convertirse a sonido
        real con SU PROPIO intervalo -- clarinete en Sib baja 2 semitonos,
        trompa en Fa baja 7 (quinta justa), nunca el mismo delta para los
        dos ni el rango dejado tal cual escrito."""
        clarinete_sonando = rango_comodo_sonando('Clarinete')
        self.assertEqual(
            (music21.pitch.Pitch(clarinete_sonando[0]).ps, music21.pitch.Pitch(clarinete_sonando[1]).ps),
            (music21.pitch.Pitch('E3').ps - 2, music21.pitch.Pitch('C6').ps - 2),
        )
        corno_sonando = rango_comodo_sonando('Corno')
        self.assertEqual(
            (music21.pitch.Pitch(corno_sonando[0]).ps, music21.pitch.Pitch(corno_sonando[1]).ps),
            (music21.pitch.Pitch('F2').ps - 7, music21.pitch.Pitch('C6').ps - 7),
        )

        # Notas bien interiores a ambos rangos (escrito y sonando), lejos de
        # cualquier margen -- lo que se verifica acá es que la transposición
        # correcta no inventa una alerta donde no la hay, no un caso límite.
        p_clarinete = self._parte_simple('Clarinete en Sib', ['B4', 'B4'])
        self.assertEqual(evaluar_viabilidad_instrumental('Clarinete en Sib', p_clarinete), [])

        p_corno = self._parte_simple('Trompa en Fa', ['C4', 'C4'])
        self.assertEqual(evaluar_viabilidad_instrumental('Trompa en Fa', p_corno), [])


class NotaMasFrecuenteYFuentePdfTests(TestCase):
    """
    Punto D del reporte de bugs sobre "Flauta exagerada": "Más frecuente"
    salía en letras crudas (C, B, G) en vez de solfeo, y el bemol/sostenido
    salía como un cuadro negro en el PDF (fuente sin esos glifos).
    """

    def test_a_solfeo_sin_octava_tambien_convierte(self):
        """Causa real del bug: PATRON_PITCH_CON_OCTAVA exigía octava
        obligatoria -- .name de music21 (usado para nota_mas_frecuente, a
        diferencia de .nameWithOctave) nunca la lleva, así que _a_solfeo
        devolvía la letra cruda sin convertir."""
        from django.utils import translation
        with translation.override('es'):
            self.assertEqual(_a_solfeo('C'), 'Do')
            self.assertEqual(_a_solfeo('B-'), 'Si♭')
            self.assertEqual(_a_solfeo('F#'), 'Fa♯')
        with translation.override('en'):
            self.assertEqual(_a_solfeo('B-'), 'B♭')

    def test_nota_mas_frecuente_end_to_end_en_solfeo(self):
        """Candado de regresión de punta a punta: calcular_estadisticas_parte
        tiene que devolver nota_mas_frecuente en solfeo, no en letra cruda."""
        from trainer.views import calcular_estadisticas_parte
        from django.utils import translation
        p = music21.stream.Part()
        m = music21.stream.Measure(number=1)
        m.append(music21.note.Note('G4', quarterLength=1.0))
        m.append(music21.note.Note('G5', quarterLength=1.0))
        m.append(music21.note.Note('C4', quarterLength=1.0))
        p.append(m)
        with translation.override('es'):
            stats = calcular_estadisticas_parte(p)
        self.assertEqual(stats['nota_mas_frecuente'], 'Sol')

    def test_fuente_unicode_se_registra_para_el_pdf_del_analizador(self):
        """orquestador_exportar_pdf tiene que registrar DejaVuSans (mismo
        mecanismo que curso_exportar_pdf) -- antes nunca se llamaba, así que
        el PDF caía a Helvetica, que dibuja ♭/♯/♮ como un cuadro negro."""
        import trainer.views as views_module
        views_module._FUENTE_UNICODE_PDF_REGISTRADA = False
        views_module._asegurar_fuente_unicode_pdf()
        self.assertTrue(views_module._FUENTE_UNICODE_PDF_REGISTRADA)
        import xhtml2pdf.context as xhtml2pdf_context
        self.assertIn('dejavusans', xhtml2pdf_context.pisaContext().fontList)

    def test_pdf_usa_dejavusans_en_el_body(self):
        from django.template.loader import render_to_string
        html = render_to_string('trainer/orquestador_pdf.html', {
            'analysis': type('FakeAnalysis', (), {'name': 'Test', 'created_at': None})(),
            'data': {'resumen_general': 'x', 'resumen_por_instrumento': [], 'estadisticas_por_instrumento': {},
                      'alertas_viabilidad': [], 'alertas_ejecucion': {}, 'bloques': []},
        })
        self.assertIn('DejaVuSans', html)


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
