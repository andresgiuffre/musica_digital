from django.contrib import admin
from .models import Game, Score, Attempt, SheetMusic, Collection, Favorite, StudySession, SheetMusicProgress, DailyGoal, UserDailyGoal

admin.site.register(Game)
admin.site.register(Score)
admin.site.register(Attempt)
admin.site.register(SheetMusic)
admin.site.register(Collection)
admin.site.register(Favorite)
admin.site.register(StudySession)
admin.site.register(SheetMusicProgress)
admin.site.register(DailyGoal)
admin.site.register(UserDailyGoal)
