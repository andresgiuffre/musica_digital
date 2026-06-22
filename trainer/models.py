from django.db import models
from django.contrib.auth.models import User

class Game(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField()
    order = models.IntegerField(default=1)
    recommended_accuracy = models.IntegerField(default=0)
    recommended_attempts = models.IntegerField(default=0)

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

    def __str__(self):
        return f"Perfil de {self.user.username}"

class Achievement(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField()
    icon = models.CharField(max_length=50, default="🏆")
    
    def __str__(self):
        return self.name

class UserAchievement(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='achievements')
    achievement = models.ForeignKey(Achievement, on_delete=models.CASCADE)
    date_earned = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'achievement')

    def __str__(self):
        return f"{self.user.username} - {self.achievement.name}"

class Piece(models.Model):
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=200, blank=True, null=True)
    time_signature = models.CharField(max_length=10, default="4/4")
    key_signature = models.CharField(max_length=50, blank=True, null=True)
    difficulty = models.IntegerField(default=1, help_text="1: Principiante, 2: Intermedio, 3: Avanzado")
    xml_content = models.TextField(help_text="Contenido en formato MusicXML")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.author}"

class Collection(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name

class SheetMusic(models.Model):
    title = models.CharField(max_length=100, blank=True, help_text="Se puede extraer automáticamente del MusicXML")
    composer = models.CharField(max_length=100, blank=True, help_text="Se puede extraer automáticamente del MusicXML")
    difficulty = models.IntegerField(default=1)
    xml_file = models.FileField(upload_to='partituras/')
    collections = models.ManyToManyField(Collection, blank=True, related_name='sheet_musics')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title or self.xml_file.name

class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'sheet_music')

class StudySession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='study_sessions')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    date = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(default=0)
    bpm_used = models.IntegerField(default=100)
    play_count = models.IntegerField(default=0)

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

class DailyGoal(models.Model):
    GOAL_TYPES = (
        ('TIME', 'Tiempo de Estudio'),
        ('PIECES', 'Partituras Completadas'),
    )
    title = models.CharField(max_length=100)
    goal_type = models.CharField(max_length=20, choices=GOAL_TYPES)
    target_value = models.IntegerField(help_text="Ej: 10 para 10 minutos")

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

class Playlist(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='playlists')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class PlaylistSheet(models.Model):
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name='items')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order']

class SheetMarker(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sheet_markers')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    measure = models.IntegerField()
    text = models.CharField(max_length=200)
    color = models.CharField(max_length=20, default='blue')
    created_at = models.DateTimeField(auto_now_add=True)

class SheetNote(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sheet_notes')
    sheet_music = models.ForeignKey(SheetMusic, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class SessionAudio(models.Model):
    session = models.OneToOneField(StudySession, on_delete=models.CASCADE, related_name='audio_eval')
    audio_file = models.FileField(upload_to='session_audio/', blank=True, null=True)
    evaluation_score = models.IntegerField(null=True, blank=True)
    feedback_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

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

    def __str__(self):
        return f"{self.user.username} - {self.chord_name}"

class MidiGameSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='midi_game_sessions')
    game_type = models.CharField(max_length=50, default='chord_identification')
    score = models.IntegerField(default=0)
    xp_earned = models.IntegerField(default=0)
    duration_seconds = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.game_type} - {self.score} pts"
