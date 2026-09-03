from django.db import models
from django.contrib.auth.models import User
from .storage import EncryptedFileSystemStorage

class Game(models.Model):
    slug = models.SlugField(unique=True, help_text="Tiene que coincidir con el slug ya cableado en las URLs del Entrenador -- crear un Game nuevo acá no crea una página nueva sola.")
    name = models.CharField(max_length=100)
    # name_en, no una fila de Game separada por idioma (a diferencia de Curso):
    # Score/Attempt tienen FK directa a este Game, y el progreso de un usuario
    # tiene que seguir siendo EL MISMO juego sin importar qué idioma tenga
    # activo -- duplicar filas por idioma (el patrón de Curso) rompería esa
    # relación. Un campo de texto extra en la misma fila resuelve la
    # traducción del nombre sin tocar ninguna FK existente.
    name_en = models.CharField(max_length=100, blank=True, help_text="Nombre en inglés. Si queda vacío, se muestra el español (name) también con idioma inglés activo.")
    description = models.TextField()
    description_en = models.TextField(blank=True, help_text="Descripción en inglés. Si queda vacío, se muestra el español (description) también con idioma inglés activo.")
    order = models.IntegerField(default=1)
    recommended_accuracy = models.IntegerField(default=0)
    recommended_attempts = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Juego (Entrenador)"
        verbose_name_plural = "Juegos (Entrenador)"

    def __str__(self):
        return self.name

    @property
    def display_name(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.name_en:
            return self.name_en
        return self.name

    @property
    def display_description(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.description_en:
            return self.description_en
        return self.description

class Score(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='scores')
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    level = models.IntegerField(default=1)
    total_points = models.IntegerField(default=0)
    max_streak = models.IntegerField(default=0)
    current_streak = models.IntegerField(default=0)
    correct_answers = models.IntegerField(default=0)
    total_answers = models.IntegerField(default=0)

    class Meta:
        unique_together = ('user', 'game')
        verbose_name = "Puntaje de Juego"
        verbose_name_plural = "Puntajes de Juego"

    @property
    def accuracy(self):
        if self.total_answers == 0:
            return 0
        return round((self.correct_answers / self.total_answers) * 100)

    @property
    def level_info(self):
        thresholds = [0, 100, 300, 600, 1000, 1500, 2100, 2800, 3600, 4500]
        xp = self.total_points
        level = 1
        for i, threshold in enumerate(thresholds):
            if xp < threshold:
                break
            level = i + 1
        
        if level <= len(thresholds) - 1:
            current_level_base = thresholds[level - 1]
            next_level_base = thresholds[level]
        else:
            current_level_base = thresholds[-1] + (level - len(thresholds)) * 1000
            next_level_base = current_level_base + 1000
            
        xp_in_level = xp - current_level_base
        xp_needed = next_level_base - current_level_base
        progress_percentage = min(100, int((xp_in_level / xp_needed) * 100))
        
        return {
            'level': level,
            'xp_in_level': xp_in_level,
            'xp_needed': xp_needed,
            'progress_percentage': progress_percentage
        }

    def __str__(self):
        return f"{self.user.username} - {self.game.name} (Level {self.level})"

class Attempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    presented_question = models.CharField(max_length=50)
    guessed_answer = models.CharField(max_length=50)
    is_correct = models.BooleanField()
    response_time_ms = models.IntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Intento de Juego"
        verbose_name_plural = "Intentos de Juego"

    def __str__(self):
        return f"{self.user.username} - {self.game.slug} - {'Correct' if self.is_correct else 'Incorrect'}"

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    total_xp = models.IntegerField(default=0)
    user_level = models.IntegerField(default=1)
    current_daily_streak = models.IntegerField(default=0)
    max_daily_streak = models.IntegerField(default=0)
    last_active_date = models.DateField(null=True, blank=True)
    max_study_time_day = models.IntegerField(default=0)
    max_sessions_day = models.IntegerField(default=0)
    creditos_analisis = models.IntegerField(default=0, help_text="Créditos del Director de Estudio (analizador de partituras con IA) asignados manualmente por el admin. No se otorgan automáticamente.")
    creditos_bonus = models.IntegerField(default=0, help_text="Créditos adicionales del Director de Estudio cargados manualmente o comprados. No se resetean ni vencen.")

    class Meta:
        verbose_name = "Perfil de Usuario"
        verbose_name_plural = "Perfiles de Usuario"

    def __str__(self):
        return f"Perfil de {self.user.username}"

class Achievement(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField()
    icon = models.CharField(max_length=50, default="🏆")

    class Meta:
        verbose_name = "Logro"
        verbose_name_plural = "Logros"

    def __str__(self):
        return self.name

class UserAchievement(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='achievements')
    achievement = models.ForeignKey(Achievement, on_delete=models.CASCADE)
    date_earned = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'achievement')
        verbose_name = "Logro Obtenido"
        verbose_name_plural = "Logros Obtenidos"

    def __str__(self):
        return f"{self.user.username} - {self.achievement.name}"

class Piece(models.Model):
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=200, blank=True, null=True)
    time_signature = models.CharField(max_length=10, default="4/4")
    key_signature = models.CharField(max_length=50, blank=True, null=True)
    difficulty = models.IntegerField(default=1, help_text="1: Principiante, 2: Intermedio, 3: Avanzado")
    xml_content = models.TextField(help_text="Contenido MusicXML pegado como texto. Alimenta el juego 'Lectura Musical' del Entrenador -- esto NO aparece en la Biblioteca de Partituras (para eso usá 'Partitura (Biblioteca)').")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Partitura de Lectura (Entrenador)"
        verbose_name_plural = "Partituras de Lectura (Entrenador)"

    def __str__(self):
        return f"{self.title} - {self.author}"

class Collection(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    class Meta:
        verbose_name = "Colección de Partituras"
        verbose_name_plural = "Colecciones de Partituras"

    def __str__(self):
        return self.name

class SheetMusic(models.Model):
    title = models.CharField(max_length=100, blank=True, help_text="Se puede extraer automáticamente del MusicXML")
    composer = models.CharField(max_length=100, blank=True, help_text="Se puede extraer automáticamente del MusicXML")
    difficulty = models.IntegerField(default=1)
    xml_file = models.FileField(upload_to='partituras/', help_text="Subí acá el archivo (MusicXML o .mxl comprimido) -- va a aparecer en la Biblioteca de Partituras del sitio.")
    collections = models.ManyToManyField(Collection, blank=True, related_name='sheet_musics')
    created_at = models.DateTimeField(auto_now_add=True)
    oculto_en_biblioteca = models.BooleanField(
        default=False,
        help_text="Para partituras subidas solo como ejemplo dentro de un curso (Ejemplo de partitura) -- "
                   "no aparecen en el listado público de la Biblioteca, pero siguen funcionando normalmente "
                   "donde ya estén referenciadas (el curso, favoritos, progreso)."
    )

    class Meta:
        verbose_name = "Partitura (Biblioteca)"
        verbose_name_plural = "Partituras (Biblioteca)"

    def __str__(self):
        return self.title or self.xml_file.name

class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'sheet_music')
        verbose_name = "Favorito de Biblioteca"
        verbose_name_plural = "Favoritos de Biblioteca"

class StudySession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='study_sessions')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    date = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(default=0)
    bpm_used = models.IntegerField(default=100)
    play_count = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Sesión de Estudio"
        verbose_name_plural = "Sesiones de Estudio"

    def __str__(self):
        return f"{self.user.username} - {self.sheet_music} - {self.date}"

class RehearsalConfig(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rehearsal_configs')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    start_measure = models.IntegerField(default=1)
    end_measure = models.IntegerField(default=1)
    start_bpm = models.IntegerField(default=60)
    end_bpm = models.IntegerField(default=100)
    bpm_step = models.IntegerField(default=5)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Configuración de Ensayo"
        verbose_name_plural = "Configuraciones de Ensayo"

    def __str__(self):
        return f"{self.name} ({self.sheet_music})"

class RehearsalLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rehearsal_logs')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    rehearsal_config = models.ForeignKey(RehearsalConfig, on_delete=models.SET_NULL, null=True, blank=True)
    repetitions_done = models.IntegerField(default=0)
    time_spent_seconds = models.IntegerField(default=0)
    max_bpm_reached = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Registro de Ensayo"
        verbose_name_plural = "Registros de Ensayo"

    def __str__(self):
        return f"Ensayo de {self.user.username} - {self.repetitions_done} reps"

class SheetMusicProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sheet_progress')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    total_time_seconds = models.IntegerField(default=0)
    total_plays = models.IntegerField(default=0)
    completion_percentage = models.IntegerField(default=0)
    last_practiced = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'sheet_music')
        verbose_name = "Progreso de Partitura"
        verbose_name_plural = "Progresos de Partitura"

class DailyGoal(models.Model):
    GOAL_TYPES = (
        ('TIME', 'Tiempo de Estudio'),
        ('PIECES', 'Partituras Completadas'),
    )
    title = models.CharField(max_length=100)
    goal_type = models.CharField(max_length=20, choices=GOAL_TYPES)
    target_value = models.IntegerField(help_text="Ej: 10 para 10 minutos")

    class Meta:
        verbose_name = "Meta Diaria (plantilla)"
        verbose_name_plural = "Metas Diarias (plantilla)"

    def __str__(self):
        return self.title

class UserDailyGoal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_goals')
    goal = models.ForeignKey(DailyGoal, on_delete=models.CASCADE)
    date = models.DateField(auto_now_add=True)
    current_value = models.IntegerField(default=0)
    is_completed = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'goal', 'date')
        verbose_name = "Meta Diaria de Usuario"
        verbose_name_plural = "Metas Diarias de Usuario"

class Playlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='playlists')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Playlist"
        verbose_name_plural = "Playlists"

    def __str__(self):
        return self.name

class PlaylistSheet(models.Model):
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name='items')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order']
        verbose_name = "Partitura en Playlist"
        verbose_name_plural = "Partituras en Playlist"

class SheetMarker(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sheet_markers')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    measure = models.IntegerField()
    text = models.CharField(max_length=200)
    color = models.CharField(max_length=20, default='blue')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Marcador de Partitura"
        verbose_name_plural = "Marcadores de Partitura"

class SheetNote(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sheet_notes')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Anotación de Partitura"
        verbose_name_plural = "Anotaciones de Partitura"

class SessionAudio(models.Model):
    session = models.OneToOneField(StudySession, on_delete=models.CASCADE, related_name='audio_eval')
    audio_file = models.FileField(upload_to='session_audio/', blank=True, null=True)
    evaluation_score = models.IntegerField(null=True, blank=True)
    feedback_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Audio de Sesión (evaluación)"
        verbose_name_plural = "Audios de Sesión (evaluación)"

class MusicalProject(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    start_date = models.DateTimeField(auto_now_add=True)
    last_practice = models.DateTimeField(auto_now=True)
    time_invested_seconds = models.IntegerField(default=0)
    max_bpm_reached = models.IntegerField(default=0)
    progress_percentage = models.IntegerField(default=0)
    last_measure = models.IntegerField(default=1)
    last_tempo = models.IntegerField(default=60)
    STATUS_CHOICES = (
        ('ACTIVE', 'Activo'),
        ('COMPLETED', 'Completado'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')

    class Meta:
        verbose_name = "Proyecto Musical"
        verbose_name_plural = "Proyectos Musicales"

    def __str__(self):
        return f"Proyecto: {self.sheet_music.title} - {self.user.username}"

class ProjectGoal(models.Model):
    project = models.ForeignKey(MusicalProject, on_delete=models.CASCADE, related_name='goals')
    GOAL_TYPES = (
        ('BPM', 'Llegar a BPM Objetivo'),
        ('TIME', 'Practicar Tiempo (min)'),
        ('COMPLETE', 'Completar Obra'),
    )
    goal_type = models.CharField(max_length=20, choices=GOAL_TYPES)
    target_value = models.IntegerField()
    is_completed = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Objetivo de Proyecto"
        verbose_name_plural = "Objetivos de Proyecto"

    def __str__(self):
        return f"{self.get_goal_type_display()} - {self.target_value}"

class ProjectSection(models.Model):
    project = models.ForeignKey(MusicalProject, on_delete=models.CASCADE, related_name='sections')
    start_measure = models.IntegerField()
    end_measure = models.IntegerField()
    STATUS_CHOICES = (
        ('MASTERED', 'Dominado'),
        ('IN_PROGRESS', 'En Progreso'),
        ('NEEDS_PRACTICE', 'Necesita Práctica'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='IN_PROGRESS')

    class Meta:
        ordering = ['start_measure']
        verbose_name = "Sección de Proyecto"
        verbose_name_plural = "Secciones de Proyecto"

    def __str__(self):
        return f"Compases {self.start_measure}-{self.end_measure} ({self.get_status_display()})"

class MidiChordStat(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='midi_chord_stats')
    chord_name = models.CharField(max_length=50) # ej: "C Maj7"
    correct_count = models.IntegerField(default=0)
    incorrect_count = models.IntegerField(default=0)
    avg_response_time_ms = models.IntegerField(default=0)
    is_mastered = models.BooleanField(default=False)
    is_problematic = models.BooleanField(default=False)
    last_played = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Estadística de Acorde MIDI"
        verbose_name_plural = "Estadísticas de Acordes MIDI"

    def __str__(self):
        return f"{self.user.username} - {self.chord_name}"

class MidiGameSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='midi_game_sessions')
    game_type = models.CharField(max_length=50, default='chord_identification')
    score = models.IntegerField(default=0)
    xp_earned = models.IntegerField(default=0)
    duration_seconds = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Sesión de Juego MIDI"
        verbose_name_plural = "Sesiones de Juego MIDI"

    def __str__(self):
        return f"{self.user.username} - {self.game_type} - {self.score} pts"

class ScoreAnalysis(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='score_analyses')
    name = models.CharField(max_length=200)
    score_file = models.FileField(
        upload_to='orquestador/scores/', storage=EncryptedFileSystemStorage(),
        help_text="Formatos: .mid, .midi, .musicxml, .mxl. Se cifra en disco con Fernet."
    )
    analysis_data = models.JSONField(blank=True, null=True, help_text="Reporte final estructurado por el agente de IA y music21")
    created_at = models.DateTimeField(auto_now_add=True)
    version_de = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='versiones',
        help_text="Análisis anterior del que este es una nueva versión (marcado explícitamente por el usuario al subir)."
    )
    share_token = models.CharField(
        max_length=43, null=True, blank=True, unique=True, db_index=True,
        help_text="Token aleatorio para el link público de solo lectura. Null = sin compartir."
    )

    # Consumo real reportado por la API de Anthropic al terminar el stream. Solo se
    # completan cuando el análisis fue exitoso (mismo criterio que el descuento de
    # crédito: hubo un tool_use_block válido) — nulos en análisis fallidos o viejos.
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    cache_creation_input_tokens = models.IntegerField(null=True, blank=True)
    cache_read_input_tokens = models.IntegerField(null=True, blank=True)

    # Puntaje determinístico (instrumentos × compases) calculado antes de llamar a la
    # API, y créditos realmente cobrados por este análisis (1 o 2). Guardarlos acá
    # evita tener que re-parsear todo el histórico para calibrar el umbral de
    # confirmación más adelante con datos reales de (puntaje, tokens).
    puntaje_obra = models.IntegerField(null=True, blank=True)
    creditos_cobrados = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "Análisis de Orquestación (Director de Estudio)"
        verbose_name_plural = "Análisis de Orquestación (Director de Estudio)"

    def __str__(self):
        return f"{self.name} - {self.user.username}"


class FragmentoOrquestacion(models.Model):
    """
    Fragmento de piano curado por un admin para el ejercicio de orquestación
    (arrastrar cada nota a un instrumento de cuerdas). Sin cifrar a diferencia de
    ScoreAnalysis.score_file: es material pedagógico compartido, no contenido
    privado de un usuario — mismo criterio que SheetMusic.xml_file.
    """
    nombre = models.CharField(max_length=200)
    archivo = models.FileField(upload_to='orquestacion_ejercicios/', help_text="Formatos: .musicxml, .xml, .mxl. Piano (una o dos manos). Subí acá el fragmento para el Ejercicio de Orquestación -- no es la Biblioteca de Partituras.")
    activo = models.BooleanField(default=True, help_text="Solo los fragmentos activos aparecen en el listado del ejercicio.")
    creado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='fragmentos_orquestacion')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Fragmento (Ejercicio de Orquestación)"
        verbose_name_plural = "Fragmentos (Ejercicio de Orquestación)"

    def __str__(self):
        return self.nombre


class Curso(models.Model):
    IDIOMA_CHOICES = [
        ('es', 'Español'),
        ('en', 'English'),
    ]

    nombre = models.CharField(max_length=200)
    nombre_en = models.CharField(max_length=200, blank=True, help_text="Versión en inglés. Si queda vacío, se muestra el nombre en español también con idioma inglés activo.")
    descripcion_corta = models.CharField(max_length=300, blank=True)
    descripcion_corta_en = models.CharField(max_length=300, blank=True, help_text="Versión en inglés. Si queda vacío, se muestra la descripción en español también con idioma inglés activo.")
    activo = models.BooleanField(default=True, help_text="Solo los cursos activos aparecen listados para los usuarios.")
    # Cursos NO se traduce con gettext -- es contenido curado a mano. El diseño
    # original era un árbol completo separado por idioma (Grado/Tema/
    # BloqueContenido colgando de un Curso con idioma='en' propio); se abandonó
    # -- ver nombre_en arriba y titulo_en en Grado/Tema/BloqueContenido -- porque
    # nunca llegó a usarse (nunca se cargó un segundo Curso en inglés) y duplicar
    # árboles enteros por una traducción es más carga de mantenimiento que un
    # campo _en por fila. `idioma`/`codigo` quedan en el modelo (agrupan
    # variantes si algún día hace falta un Curso genuinamente distinto por
    # idioma, no solo traducido) pero cursos_list/curso_detail ya NO filtran
    # por idioma -- ver el comentario en views.py.
    idioma = models.CharField(max_length=2, choices=IDIOMA_CHOICES, default='es')
    codigo = models.SlugField(max_length=140, help_text=(
        "Identificador estable compartido entre versiones de idioma del mismo curso "
        "(ej. 'teoria-musical'). NO se traduce -- solo agrupa variantes de idioma "
        "del mismo curso entre sí."
    ))

    class Meta:
        unique_together = ('codigo', 'idioma')
        verbose_name = "Curso"
        verbose_name_plural = "Cursos"

    def __str__(self):
        return f"{self.nombre} ({self.idioma})"

    @property
    def nombre_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.nombre_en:
            return self.nombre_en
        return self.nombre

    @property
    def descripcion_corta_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.descripcion_corta_en:
            return self.descripcion_corta_en
        return self.descripcion_corta


class Grado(models.Model):
    curso = models.ForeignKey(Curso, on_delete=models.CASCADE, related_name='grados')
    numero = models.PositiveIntegerField(help_text='Orden dentro del curso Y etiqueta visible ("Grado 0", "Grado 1", ...).')
    titulo = models.CharField(max_length=200, help_text='Ej: "Fundamentos de lectura".')
    # Mismo patrón que BloqueContenido.texto_markdown_en -- ver ahí el porqué
    # (contenido bilingüe en la misma fila, no un Curso separado por idioma).
    titulo_en = models.CharField(max_length=200, blank=True, help_text="Versión en inglés. Si queda vacío, se muestra el título en español también con idioma inglés activo.")
    activo = models.BooleanField(default=True, help_text="Solo los grados activos aparecen listados para los usuarios.")

    class Meta:
        unique_together = ('curso', 'numero')
        ordering = ['numero']
        verbose_name = "Grado"
        verbose_name_plural = "Grados"

    def __str__(self):
        return f"Grado {self.numero} - {self.titulo} ({self.curso.nombre})"

    @property
    def titulo_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.titulo_en:
            return self.titulo_en
        return self.titulo


class Tema(models.Model):
    grado = models.ForeignKey(Grado, on_delete=models.CASCADE, related_name='temas')
    titulo = models.CharField(max_length=200)
    titulo_en = models.CharField(max_length=200, blank=True, help_text="Versión en inglés. Si queda vacío, se muestra el título en español también con idioma inglés activo.")
    orden = models.PositiveIntegerField(default=0, help_text="Orden dentro del grado.")
    slug = models.SlugField(help_text="Se escribe a mano, no se autogenera (misma convención que Game.slug/Achievement.slug/Collection.slug). Único dentro del grado -- se usa en la URL del tema.")
    activo = models.BooleanField(default=True, help_text="Solo los temas activos son accesibles para los usuarios.")

    class Meta:
        unique_together = ('grado', 'slug')
        ordering = ['orden']
        verbose_name = "Tema"
        verbose_name_plural = "Temas"

    def __str__(self):
        return f"{self.titulo} ({self.grado})"

    @property
    def titulo_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.titulo_en:
            return self.titulo_en
        return self.titulo


class BloqueContenido(models.Model):
    """
    Un bloque de contenido dentro de un Tema. `tipo` determina cuáles de los campos
    de abajo son relevantes -- todos nullable/blank a propósito, cada fila llena
    solo el subconjunto que le corresponde a su tipo (ver clean()). Diseñado para
    poder sumar un tipo QUIZ más adelante sin reestructurar: alcanza con un valor
    más en TIPO_CHOICES y su propio grupo de campos nullable, igual que conviven
    hoy EJEMPLO_PARTITURA y PRACTICA.
    """
    TEXTO = 'TEXTO'
    EJEMPLO_PARTITURA = 'EJEMPLO_PARTITURA'
    PRACTICA = 'PRACTICA'
    IMAGEN = 'IMAGEN'
    VIDEO = 'VIDEO'
    PRACTICA_DIRIGIDA = 'PRACTICA_DIRIGIDA'
    TIPO_CHOICES = (
        (TEXTO, 'Texto'),
        (EJEMPLO_PARTITURA, 'Ejemplo de partitura'),
        (PRACTICA, 'Práctica'),
        (IMAGEN, 'Imagen'),
        (VIDEO, 'Video'),
        (PRACTICA_DIRIGIDA, 'Práctica dirigida'),
    )

    MODO_IDENTIFICAR_NOTAS = 'identificar_notas'
    MODO_PRACTICA_CHOICES = (
        (MODO_IDENTIFICAR_NOTAS, 'Identificar notas'),
    )

    FUENTE_VIDEO_ARCHIVO = 'ARCHIVO'
    FUENTE_VIDEO_EMBED = 'EMBED'
    FUENTE_VIDEO_CHOICES = (
        (FUENTE_VIDEO_ARCHIVO, 'Archivo subido (MP4/WebM)'),
        (FUENTE_VIDEO_EMBED, 'YouTube / Vimeo'),
    )

    tema = models.ForeignKey(Tema, on_delete=models.CASCADE, related_name='bloques')
    orden = models.PositiveIntegerField(default=0)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)

    # --- TEXTO ---
    texto_markdown = models.TextField(blank=True, help_text="Fuente Markdown (nunca HTML crudo). Se sanitiza y renderiza en el detalle del tema.")
    # _en en la MISMA fila, no un Curso separado por idioma -- mismo patrón que
    # Game.name_en (ver ahí el porqué). Acá aplica más fuerte todavía: un Tema
    # entero duplicado por idioma implicaría mantener dos árboles Grado/Tema/
    # BloqueContenido en paralelo por cada curso bilingüe. Blank/nullable a
    # propósito -- si queda vacío, texto_markdown_mostrado devuelve el español.
    texto_markdown_en = models.TextField(blank=True, help_text="Fuente Markdown en inglés. Si queda vacío, se muestra el texto en español también con idioma inglés activo.")

    # --- EJEMPLO_PARTITURA (exactamente uno de los dos FK de abajo) ---
    sheet_music = models.ForeignKey(
        SheetMusic, on_delete=models.SET_NULL, null=True, blank=True, related_name='bloques_curso',
        help_text="Partitura de la Biblioteca a mostrar como ejemplo. Exactamente uno entre esto y 'fragmento orquestación' cuando el tipo es Ejemplo de partitura."
    )
    fragmento_orquestacion = models.ForeignKey(
        FragmentoOrquestacion, on_delete=models.SET_NULL, null=True, blank=True, related_name='bloques_curso',
        help_text="Fragmento del Ejercicio de Orquestación a mostrar como ejemplo. Exactamente uno entre esto y 'sheet music' cuando el tipo es Ejemplo de partitura."
    )
    contexto_ejemplo = models.CharField(max_length=300, blank=True, help_text='Texto corto opcional arriba del ejemplo, ej: "En este ejemplo, fijate cómo...".')
    contexto_ejemplo_en = models.CharField(max_length=300, blank=True, help_text="Versión en inglés. Si queda vacío, se muestra el texto en español también con idioma inglés activo.")
    # Controlan qué metadata embebida en el MusicXML renderiza OSMD además del
    # pentagrama -- independiente de qué haya elegido el usuario al exportar desde
    # MuseScore (ej. "sin encabezado" solo oculta el título/subtítulo visual del
    # PDF, pero el nombre de instrumento (<part-name>) y el compositor/arreglador
    # (<creator>) quedan igual embebidos en el XML y OSMD los dibuja salvo que se
    # le indique explícitamente que no). True = mostrar (default, no cambia el
    # renderizado de bloques ya creados). Ver tema_detail.html para el mapeo a
    # las opciones IOSMDOptions correspondientes.
    mostrar_nombre_instrumento = models.BooleanField(default=True, help_text="Si está destildado, oculta el nombre del instrumento junto al pentagrama.")
    mostrar_compositor = models.BooleanField(default=True, help_text="Si está destildado, oculta el subtítulo/compositor/arreglador embebidos en el archivo.")
    mostrar_letra = models.BooleanField(default=True, help_text="Si está destildado, oculta la letra (lyrics) que venga incluida en la partitura.")
    # Default False (compactar, el comportamiento de siempre de OSMD) para no
    # cambiar el renderizado de bloques ya creados -- mismo criterio que los
    # tres de arriba. Confirmado leyendo el código fuente de OSMD 1.8.8 (la
    # versión pineada en el sitio): autoGenerateMultipleRestMeasuresFromRestMeasures
    # (default true en OSMD) es lo que junta compases vacíos consecutivos en un
    # solo silencio con un número arriba -- tildar esto lo desactiva para ESTE
    # bloque puntual, sin tocar el resto del sitio.
    mostrar_compases_vacios_literal = models.BooleanField(default=False, help_text="Si está tildado, NO compacta compases vacíos consecutivos en un silencio grande -- los renderiza uno por uno, literal.")

    # --- PRACTICA ---
    practica_texto = models.CharField(max_length=300, blank=True, help_text='Ej: "Practicá esto en Identificación de Notas". Requerido cuando el tipo es Práctica.')
    practica_texto_en = models.CharField(max_length=300, blank=True, help_text="Versión en inglés. Si queda vacío, se muestra el texto en español también con idioma inglés activo.")
    practica_url = models.CharField(
        max_length=300, blank=True,
        help_text="URL/path opcional escrito a mano hacia una página existente (ej: /juego/notas/). Si queda vacío, se muestra como texto plano sin link. Deliberadamente NO es una FK a Game ni a nada más."
    )
    practica_url_en = models.CharField(
        max_length=300, blank=True,
        help_text="Versión en inglés del path, SOLO si difiere del de arriba -- casi siempre porque necesita el prefijo /en/ (ver i18n_patterns en config/urls.py: una URL sin prefijo fuerza español sin importar el idioma activo). Si queda vacío, se usa el mismo path de arriba tal cual."
    )

    # --- IMAGEN ---
    imagen = models.FileField(
        upload_to='cursos_imagenes/', null=True, blank=True,
        help_text="PNG, JPG o SVG -- para diagramas/fotos que no se pueden representar como MusicXML. Requerido cuando el tipo es Imagen."
    )
    # A diferencia de los demás campos _en (que traducen texto), acá el ARCHIVO
    # entero puede cambiar por idioma -- ej. un diagrama con rótulos en español
    # vs. el mismo diagrama con rótulos en inglés. Opcional: si queda vacío,
    # imagen_mostrada() cae a la imagen en español igual que el resto del patrón.
    imagen_en = models.FileField(
        upload_to='cursos_imagenes/', null=True, blank=True,
        help_text="Versión en inglés (mismo diagrama con texto embebido en inglés, si aplica). Si queda vacío, se muestra la imagen en español también con idioma inglés activo."
    )
    # Un solo campo hace doble función -- pie de imagen visible Y atributo alt
    # del <img> (accesibilidad) -- en vez de dos campos separados, para no
    # duplicar lo que en la práctica casi siempre es el mismo texto ("Círculo
    # de quintas", "Posición de la mano en Do mayor", etc.). Mismo patrón _en
    # que el resto de los campos de texto del bloque.
    contexto_imagen = models.CharField(max_length=300, blank=True, help_text="Pie de imagen (también se usa como texto alternativo/accesibilidad).")
    contexto_imagen_en = models.CharField(max_length=300, blank=True, help_text="Versión en inglés. Si queda vacío, se muestra el texto en español también con idioma inglés activo.")

    # --- VIDEO (exactamente uno de los dos, según video_fuente) ---
    video_fuente = models.CharField(max_length=20, choices=FUENTE_VIDEO_CHOICES, blank=True, help_text="Requerido cuando el tipo es Video.")
    video_archivo = models.FileField(
        upload_to='cursos_videos/', null=True, blank=True,
        help_text="MP4 (recomendado) o WebM -- son los únicos formatos que reproducen todos los navegadores sin conversión. AVI/MOV/MKV no van a andar aunque los subas. Requerido cuando la fuente es Archivo subido."
    )
    # Mismo criterio que imagen_en: archivo completo distinto por idioma (ej.
    # video grabado o subtitulado en inglés), no solo texto. Opcional.
    video_archivo_en = models.FileField(
        upload_to='cursos_videos/', null=True, blank=True,
        help_text="Versión en inglés (archivo distinto, ej. grabado o subtitulado en inglés). Si queda vacío, se muestra el video en español también con idioma inglés activo."
    )
    video_embed_url = models.CharField(
        max_length=500, blank=True,
        help_text="Pegá el link tal cual lo copiaste del navegador (youtube.com/watch?v=..., youtu.be/..., vimeo.com/...). Se convierte solo al formato de embed restringido. Requerido cuando la fuente es YouTube/Vimeo."
    )
    video_embed_url_en = models.CharField(
        max_length=500, blank=True,
        help_text="Versión en inglés (link a un video de YouTube/Vimeo distinto). Si queda vacío, se muestra el video en español también con idioma inglés activo."
    )
    contexto_video = models.CharField(max_length=300, blank=True, help_text="Texto corto opcional arriba del video.")
    contexto_video_en = models.CharField(max_length=300, blank=True, help_text="Versión en inglés. Si queda vacío, se muestra el texto en español también con idioma inglés activo.")

    # --- PRACTICA_DIRIGIDA ---
    # Sin _en: a diferencia de imagen/video, un ejercicio de identificar notas no
    # tiene texto ni audio que traducir -- las mismas notas sirven para cualquier
    # idioma (los NOMBRES de nota ya se traducen aparte, ver la regla ya
    # establecida de LANGUAGE_CODE == 'en' en trainer_notas.html/gabinete.css).
    # Sin cifrado (a diferencia de ScoreAnalysis.score_file) -- es contenido
    # público del curso, subido solo por el admin, mismo criterio que imagen/
    # video_archivo de este mismo modelo.
    musicxml_practica = models.FileField(
        upload_to='cursos_practica_dirigida/', null=True, blank=True,
        help_text="MusicXML acotado (2-8 compases) para el ejercicio de práctica dirigida. Requerido cuando el tipo es Práctica dirigida."
    )
    modo_practica = models.CharField(
        max_length=30, choices=MODO_PRACTICA_CHOICES, blank=True, default=MODO_IDENTIFICAR_NOTAS,
        help_text="Qué le pide el ejercicio al alumno. Por ahora solo 'Identificar notas' -- el campo queda abierto a sumar otros modos más adelante sin migrar de nuevo."
    )
    precision_minima = models.PositiveSmallIntegerField(
        default=80,
        help_text="Porcentaje de aciertos (0-100) para marcar el intento como completado. Editable por bloque por si algún tema necesita ser más permisivo."
    )

    class Meta:
        ordering = ['orden']
        verbose_name = "Bloque de Contenido"
        verbose_name_plural = "Bloques de Contenido"

    def __str__(self):
        return f"{self.get_tipo_display()} #{self.orden} - {self.tema}"

    # Mismo patrón que Game.display_name: devuelve la versión _en solo si el
    # idioma activo es inglés Y está cargada -- si no, cae al español siempre.
    @property
    def texto_markdown_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.texto_markdown_en:
            return self.texto_markdown_en
        return self.texto_markdown

    @property
    def contexto_ejemplo_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.contexto_ejemplo_en:
            return self.contexto_ejemplo_en
        return self.contexto_ejemplo

    @property
    def practica_texto_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.practica_texto_en:
            return self.practica_texto_en
        return self.practica_texto

    @property
    def practica_url_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.practica_url_en:
            return self.practica_url_en
        return self.practica_url

    @property
    def contexto_imagen_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.contexto_imagen_en:
            return self.contexto_imagen_en
        return self.contexto_imagen

    @property
    def imagen_mostrada(self):
        """Devuelve el FieldFile a servir -- imagen_en si el idioma activo es
        inglés Y está cargada, si no la imagen en español siempre."""
        from django.utils.translation import get_language
        if get_language() == 'en' and self.imagen_en:
            return self.imagen_en
        return self.imagen

    @property
    def contexto_video_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.contexto_video_en:
            return self.contexto_video_en
        return self.contexto_video

    @property
    def video_archivo_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.video_archivo_en:
            return self.video_archivo_en
        return self.video_archivo

    @property
    def video_embed_url_mostrado(self):
        from django.utils.translation import get_language
        if get_language() == 'en' and self.video_embed_url_en:
            return self.video_embed_url_en
        return self.video_embed_url

    @property
    def video_embed_info(self):
        """
        None si no aplica (no es un bloque VIDEO con fuente EMBED) o si la URL
        cargada no matchea YouTube/Vimeo -- clean() ya debería haber rechazado
        esto último al guardar, pero el template no debería asumirlo y romper
        si de todos modos llega una fila inválida (ej. carga por fixture/shell
        sin pasar por full_clean())."""
        if self.tipo != self.VIDEO or self.video_fuente != self.FUENTE_VIDEO_EMBED or not self.video_embed_url_mostrado:
            return None
        from .services import extraer_video_embed
        return extraer_video_embed(self.video_embed_url_mostrado)

    def clean(self):
        from django.core.exceptions import ValidationError
        errores = {}

        if self.tipo == self.TEXTO:
            if not self.texto_markdown:
                errores['texto_markdown'] = "Requerido cuando el tipo es Texto."
            if self.sheet_music_id or self.fragmento_orquestacion_id:
                errores['tipo'] = "No debería haber partitura/fragmento asignado en un bloque de Texto."
            if self.practica_texto:
                errores['practica_texto'] = "No debería llenarse en un bloque de Texto."
            if self.imagen or self.imagen_en:
                errores['imagen'] = "No debería llenarse en un bloque de Texto."
            if self.video_archivo or self.video_archivo_en or self.video_embed_url or self.video_embed_url_en:
                errores['video_fuente'] = "No debería llenarse en un bloque de Texto."
            if self.musicxml_practica:
                errores['musicxml_practica'] = "No debería llenarse en un bloque de Texto."

        elif self.tipo == self.EJEMPLO_PARTITURA:
            if bool(self.sheet_music_id) == bool(self.fragmento_orquestacion_id):
                # cubre "ninguno de los dos" y "los dos a la vez"
                errores['sheet_music'] = "Elegí exactamente una: partitura de biblioteca O fragmento de orquestación, no ninguna ni las dos."
            if self.texto_markdown:
                errores['texto_markdown'] = "No debería llenarse acá (usá 'contexto_ejemplo' para texto corto)."
            if self.practica_texto:
                errores['practica_texto'] = "No debería llenarse en un bloque de Ejemplo de partitura."
            if self.imagen or self.imagen_en:
                errores['imagen'] = "No debería llenarse en un bloque de Ejemplo de partitura."
            if self.video_archivo or self.video_archivo_en or self.video_embed_url or self.video_embed_url_en:
                errores['video_fuente'] = "No debería llenarse en un bloque de Ejemplo de partitura."
            if self.musicxml_practica:
                errores['musicxml_practica'] = "No debería llenarse en un bloque de Ejemplo de partitura."

        elif self.tipo == self.PRACTICA:
            if not self.practica_texto:
                errores['practica_texto'] = "Requerido cuando el tipo es Práctica."
            if self.texto_markdown:
                errores['texto_markdown'] = "No debería llenarse en un bloque de Práctica."
            if self.sheet_music_id or self.fragmento_orquestacion_id:
                errores['tipo'] = "No debería haber partitura/fragmento asignado en un bloque de Práctica."
            if self.imagen or self.imagen_en:
                errores['imagen'] = "No debería llenarse en un bloque de Práctica."
            if self.video_archivo or self.video_archivo_en or self.video_embed_url or self.video_embed_url_en:
                errores['video_fuente'] = "No debería llenarse en un bloque de Práctica."
            if self.musicxml_practica:
                errores['musicxml_practica'] = "No debería llenarse en un bloque de Práctica."

        elif self.tipo == self.IMAGEN:
            if not self.imagen:
                errores['imagen'] = "Requerido cuando el tipo es Imagen (la versión en inglés es opcional)."
            if self.texto_markdown:
                errores['texto_markdown'] = "No debería llenarse en un bloque de Imagen."
            if self.sheet_music_id or self.fragmento_orquestacion_id:
                errores['tipo'] = "No debería haber partitura/fragmento asignado en un bloque de Imagen."
            if self.practica_texto:
                errores['practica_texto'] = "No debería llenarse en un bloque de Imagen."
            if self.video_archivo or self.video_archivo_en or self.video_embed_url or self.video_embed_url_en:
                errores['video_fuente'] = "No debería llenarse en un bloque de Imagen."
            if self.musicxml_practica:
                errores['musicxml_practica'] = "No debería llenarse en un bloque de Imagen."

        elif self.tipo == self.VIDEO:
            if not self.video_fuente:
                errores['video_fuente'] = "Requerido cuando el tipo es Video."
            elif self.video_fuente == self.FUENTE_VIDEO_ARCHIVO:
                if not self.video_archivo:
                    errores['video_archivo'] = "Requerido cuando la fuente es Archivo subido (la versión en inglés es opcional)."
                if self.video_embed_url or self.video_embed_url_en:
                    errores['video_embed_url'] = "No debería llenarse si la fuente es Archivo subido."
            elif self.video_fuente == self.FUENTE_VIDEO_EMBED:
                if self.video_archivo or self.video_archivo_en:
                    errores['video_archivo'] = "No debería llenarse si la fuente es YouTube/Vimeo."
                if not self.video_embed_url:
                    errores['video_embed_url'] = "Requerido cuando la fuente es YouTube/Vimeo (la versión en inglés es opcional)."
                else:
                    from .services import extraer_video_embed
                    if not extraer_video_embed(self.video_embed_url):
                        errores['video_embed_url'] = "No reconozco un link de YouTube o Vimeo válido en esta URL."
                if self.video_embed_url_en:
                    from .services import extraer_video_embed
                    if not extraer_video_embed(self.video_embed_url_en):
                        errores['video_embed_url_en'] = "No reconozco un link de YouTube o Vimeo válido en esta URL."
            if self.texto_markdown:
                errores['texto_markdown'] = "No debería llenarse en un bloque de Video."
            if self.sheet_music_id or self.fragmento_orquestacion_id:
                errores['tipo'] = "No debería haber partitura/fragmento asignado en un bloque de Video."
            if self.practica_texto:
                errores['practica_texto'] = "No debería llenarse en un bloque de Video."
            if self.imagen or self.imagen_en:
                errores['imagen'] = "No debería llenarse en un bloque de Video."
            if self.musicxml_practica:
                errores['musicxml_practica'] = "No debería llenarse en un bloque de Video."

        elif self.tipo == self.PRACTICA_DIRIGIDA:
            if not self.musicxml_practica:
                errores['musicxml_practica'] = "Requerido cuando el tipo es Práctica dirigida."
            if self.texto_markdown:
                errores['texto_markdown'] = "No debería llenarse en un bloque de Práctica dirigida."
            if self.sheet_music_id or self.fragmento_orquestacion_id:
                errores['tipo'] = "No debería haber partitura/fragmento asignado en un bloque de Práctica dirigida."
            if self.practica_texto:
                errores['practica_texto'] = "No debería llenarse en un bloque de Práctica dirigida."
            if self.imagen or self.imagen_en:
                errores['imagen'] = "No debería llenarse en un bloque de Práctica dirigida."
            if self.video_archivo or self.video_archivo_en or self.video_embed_url or self.video_embed_url_en:
                errores['video_fuente'] = "No debería llenarse en un bloque de Práctica dirigida."

        if errores:
            raise ValidationError(errores)


class PracticaDirigidaProgreso(models.Model):
    """
    Progreso AGREGADO por usuario y por bloque de Práctica dirigida -- mismo
    espíritu que SheetMusicProgress (mejor resultado + contador de veces), no el
    de Attempt/Score (log de cada respuesta individual): este piloto no necesita
    saber qué nota puntual falló cada vez, alcanza con el resultado final de
    cada intento completo de la pieza.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    bloque = models.ForeignKey(BloqueContenido, on_delete=models.CASCADE)
    mejor_precision = models.FloatField(default=0)  # 0-100
    notas_totales = models.PositiveIntegerField(default=0, help_text="Cantidad de notas del último intento.")
    veces_practicado = models.PositiveIntegerField(default=0)
    completado = models.BooleanField(default=False)
    ultima_vez = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'bloque')
        verbose_name = "Progreso de Práctica Dirigida"
        verbose_name_plural = "Progresos de Práctica Dirigida"
