from django.contrib import admin
from .models import Game, Score, Attempt, SheetMusic

admin.site.register(Game)
admin.site.register(Score)
admin.site.register(Attempt)
admin.site.register(SheetMusic)
