from django.db import models
from django.contrib.auth.models import User

class Game(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField()

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
