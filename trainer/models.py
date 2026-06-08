from django.db import models
from django.contrib.auth.models import User

class Exercise(models.Model):
    name = models.CharField(max_length=100)
    clef = models.CharField(max_length=20, default='treble')
    min_note = models.CharField(max_length=5, default='C4')
    max_note = models.CharField(max_length=5, default='G5')

    def __str__(self):
        return self.name

class Score(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    level = models.IntegerField(default=1)
    total_points = models.IntegerField(default=0)
    max_streak = models.IntegerField(default=0)
    current_streak = models.IntegerField(default=0)
    correct_answers = models.IntegerField(default=0)
    total_answers = models.IntegerField(default=0)

    @property
    def accuracy(self):
        if self.total_answers == 0:
            return 0
        return round((self.correct_answers / self.total_answers) * 100)

    def __str__(self):
        return f"{self.user.username} - Level {self.level}"

class Attempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    presented_note = models.CharField(max_length=5)
    guessed_note = models.CharField(max_length=5)
    is_correct = models.BooleanField()
    response_time_ms = models.IntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.presented_note} - {'Correct' if self.is_correct else 'Incorrect'}"
