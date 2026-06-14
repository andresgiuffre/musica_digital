from django.contrib import admin
from .models import (
    Game, Score, Attempt, SheetMusic, Collection, Favorite, StudySession, 
    SheetMusicProgress, DailyGoal, UserDailyGoal, Playlist, PlaylistSheet, 
    SheetMarker, SheetNote, SessionAudio, RehearsalConfig, RehearsalLog,
    MusicalProject, ProjectGoal, ProjectSection,
    MidiChordStat, MidiGameSession
)

class PlaylistSheetInline(admin.TabularInline):
    model = PlaylistSheet
    extra = 1

@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    inlines = [PlaylistSheetInline]

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
admin.site.register(SheetMarker)
admin.site.register(SheetNote)
admin.site.register(SessionAudio)
admin.site.register(RehearsalConfig)
admin.site.register(RehearsalLog)
admin.site.register(MusicalProject)
admin.site.register(ProjectGoal)
admin.site.register(ProjectSection)
admin.site.register(MidiChordStat)
admin.site.register(MidiGameSession)

