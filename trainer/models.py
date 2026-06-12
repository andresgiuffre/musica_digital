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
    duration_seconds = models.IntegerField(default=0)
    bpm_used = models.IntegerField(default=100)
    play_count = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.user.username} estudió {self.sheet_music.title}"

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
