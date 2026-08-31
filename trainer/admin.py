from django import forms
from django.contrib import admin, messages
from django.db.models import F
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html
from .models import (
    Game, Score, Attempt, SheetMusic, Collection, Favorite, StudySession,
    SheetMusicProgress, DailyGoal, UserDailyGoal, Playlist, PlaylistSheet,
    SheetMarker, SheetNote, SessionAudio, RehearsalConfig, RehearsalLog,
    MusicalProject, ProjectGoal, ProjectSection,
    MidiChordStat, MidiGameSession, UserProfile, FragmentoOrquestacion, ScoreAnalysis,
    Curso, Grado, Tema, BloqueContenido,
)

class PlaylistSheetInline(admin.TabularInline):
    model = PlaylistSheet
    extra = 1

@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    inlines = [PlaylistSheetInline]


class GradoInline(admin.TabularInline):
    model = Grado
    extra = 1
    fields = ('numero', 'titulo', 'activo')


@admin.register(Curso)
class CursoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'idioma', 'codigo', 'activo')
    list_editable = ('activo',)
    inlines = [GradoInline]


class TemaInline(admin.TabularInline):
    model = Tema
    extra = 1
    fields = ('orden', 'titulo', 'slug', 'activo')


@admin.register(Grado)
class GradoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'numero', 'curso', 'activo')
    list_editable = ('activo',)
    inlines = [TemaInline]


class BloqueContenidoInline(admin.StackedInline):
    """
    StackedInline, no Tabular (a diferencia del único otro inline del proyecto,
    PlaylistSheetInline): BloqueContenido tiene varios campos tipo-específicos que
    quedarían vacíos en su mayoría por fila en una tabla. Los fieldsets agrupan
    visualmente qué campos son de cada tipo -- Tabular no soporta eso.
    """
    model = BloqueContenido
    extra = 1
    fieldsets = (
        (None, {'fields': ('orden', 'tipo')}),
        ('Texto', {'fields': ('texto_markdown', 'texto_markdown_en'), 'classes': ('collapse',)}),
        ('Ejemplo de partitura', {
            'fields': (
                'sheet_music', 'fragmento_orquestacion', 'contexto_ejemplo', 'contexto_ejemplo_en',
                'mostrar_nombre_instrumento', 'mostrar_compositor', 'mostrar_letra',
            ),
            'classes': ('collapse',),
        }),
        ('Práctica', {'fields': ('practica_texto', 'practica_texto_en', 'practica_url', 'practica_url_en'), 'classes': ('collapse',)}),
    )


@admin.register(Tema)
class TemaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'grado', 'orden', 'activo', 'ver_renderizado')
    list_editable = ('orden', 'activo')
    inlines = [BloqueContenidoInline]

    def ver_renderizado(self, obj):
        url = reverse('tema_detail', args=[obj.grado.curso_id, obj.grado.numero, obj.slug])
        return format_html('<a href="{}" target="_blank">Ver cómo se renderiza ↗</a>', url)
    ver_renderizado.short_description = "Vista previa"


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


@admin.register(ScoreAnalysis)
class ScoreAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'user', 'created_at', 'puntaje_obra', 'creditos_cobrados',
        'input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens',
    )
    readonly_fields = (
        'created_at', 'puntaje_obra', 'creditos_cobrados',
        'input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens',
    )
    search_fields = ('name', 'user__username')

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
admin.site.register(FragmentoOrquestacion)

