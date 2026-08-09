from django.db import models
from django.contrib.auth.models import User
from .storage import EncryptedFileSystemStorage

class Game(models.Model):
    slug = models.SlugField(unique=True, help_text="Tiene que coincidir con el slug ya cableado en las URLs del Entrenador -- crear un Game nuevo acá no crea una página nueva sola.")
    name = models.CharField(max_length=100)
    description = models.TextField()
    order = models.IntegerField(default=1)
    recommended_accuracy = models.IntegerField(default=0)
    recommended_attempts = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Juego (Entrenador)"
        verbose_name_plural = "Juegos (Entrenador)"

    def __str__(self):
        return self.name

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
        return f"{self.user.username} - {self.sheet_music} - {self.start_time}"

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
