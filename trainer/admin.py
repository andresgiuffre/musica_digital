from django import forms
from django.contrib import admin, messages
from django.db.models import F
from django.shortcuts import render
from .models import (
    Game, Score, Attempt, SheetMusic, Collection, Favorite, StudySession,
    SheetMusicProgress, DailyGoal, UserDailyGoal, Playlist, PlaylistSheet,
    SheetMarker, SheetNote, SessionAudio, RehearsalConfig, RehearsalLog,
    MusicalProject, ProjectGoal, ProjectSection,
    MidiChordStat, MidiGameSession, UserProfile
)

class PlaylistSheetInline(admin.TabularInline):
    model = PlaylistSheet
    extra = 1

@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    inlines = [PlaylistSheetInline]


class AgregarCreditosBonusForm(forms.Form):
    cantidad = forms.IntegerField(label="Cantidad a agregar", min_value=1)


@admin.action(description="Agregar créditos bonus")
def agregar_creditos_bonus(modeladmin, request, queryset):
    form = None
    if 'apply' in request.POST:
        form = AgregarCreditosBonusForm(request.POST)
        if form.is_valid():
            cantidad = form.cleaned_data['cantidad']
            total_usuarios = queryset.count()
            queryset.update(creditos_bonus=F('creditos_bonus') + cantidad)
            modeladmin.message_user(
                request,
                f"Se agregaron {cantidad} créditos bonus a {total_usuarios} usuario(s).",
                messages.SUCCESS,
            )
            return None

    if form is None:
        form = AgregarCreditosBonusForm()

    return render(request, 'admin/agregar_creditos_bonus.html', {
        'profiles': queryset,
        'form': form,
        'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
    })


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'creditos_analisis', 'creditos_bonus', 'total_xp', 'user_level')
    list_editable = ('creditos_analisis',)
    search_fields = ('user__username', 'user__email')
    actions = [agregar_creditos_bonus]

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

