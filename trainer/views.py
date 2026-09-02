import json
import re
import logging
import math
import os
import io
import copy
import pathlib
import zipfile
import secrets
from collections import Counter
from django.utils import timezone
import anthropic
from xhtml2pdf import pisa
from .prompts import GUIA_ESTILO_ORQUESTAL
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse, HttpResponse, StreamingHttpResponse, FileResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.db.models import Avg
from django.contrib import messages
from .models import Game, Score, Attempt, UserProfile, Achievement, UserAchievement, Piece, SheetMusic, Collection, Favorite, StudySession, SheetMusicProgress, DailyGoal, UserDailyGoal

logger = logging.getLogger(__name__)

def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            UserProfile.objects.create(user=user)
            login(request, user)
            return redirect('dashboard')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})

def get_user_level_for_xp(xp):
    if xp < 100: return 1
    if xp < 250: return 2
    if xp < 500: return 3
    if xp < 800: return 4
    if xp < 1200: return 5
    if xp < 1700: return 6
    if xp < 2300: return 7
    if xp < 3000: return 8
    if xp < 4000: return 9
    return 10 + (xp - 4000) // 1000

def check_achievements(user):
    profile = user.profile
    scores = Score.objects.filter(user=user)
    
    total_correct = sum(s.correct_answers for s in scores)
    total_answers = sum(s.total_answers for s in scores)
    
    def unlock(slug):
        try:
            ach = Achievement.objects.get(slug=slug)
            UserAchievement.objects.get_or_create(user=user, achievement=ach)
        except Achievement.DoesNotExist:
            pass

    if total_answers >= 1:
        unlock('primer-paso')
    if total_correct >= 10:
        unlock('10-correctas')
    if profile.max_daily_streak >= 7:
        unlock('constancia')

    for score in scores:
        if score.correct_answers >= 20:
            unlock('velocista')
        if score.game.slug == 'notas' and score.total_answers >= 50 and score.accuracy >= 80:
            unlock('maestro-notas')
        if score.game.slug == 'intervalos' and score.total_answers >= 50 and score.accuracy >= 75:
            unlock('explorador-intervalos')
        if score.game.slug == 'intervalos-auditivos' and score.total_answers >= 50 and score.accuracy >= 70:
            unlock('oido-entrenado')

def get_user_progress(user):
    games = Game.objects.all().order_by('order')
    progress = []
    
    for game in games:
        score, _ = Score.objects.get_or_create(user=user, game=game)
        
        completed = False
        if score.total_answers >= game.recommended_attempts:
            if score.total_answers > 0 and score.accuracy >= game.recommended_accuracy:
                completed = True
                
        progress.append({
            'game': game,
            'url_name': f"trainer_{game.slug.replace('-', '_')}",
            'score': score,
            'unlocked': True,  # Everyone is unlocked now
            'completed': completed,
            'accuracy': score.accuracy
        })
        
    return progress

# restrict_if_locked is no longer used, but let's just delete its body or references.

@login_required
def dashboard(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    today = timezone.localdate()
    if profile.last_active_date:
        delta = (today - profile.last_active_date).days
        if delta > 1:
            profile.current_daily_streak = 0
            profile.save()
            
    today = timezone.now().date()
    profile, created = UserProfile.objects.get_or_create(user=request.user)
    
    # Check current daily goal
    today = timezone.now().date()
    today_goals = UserDailyGoal.objects.filter(user=request.user, date=today)
    
    # 1. Update personal records on load just in case
    from .services import update_personal_records, get_weekly_summary, get_study_recommendations
    update_personal_records(request.user)
    
    # 2. Get recommendations and weekly summary
    recommendations = get_study_recommendations(request.user)
    weekly_summary = get_weekly_summary(request.user)
    
    # 3. GitHub Calendar Data
    from .models import StudySession
    import datetime
    from django.db.models import Sum
    one_year_ago = today - datetime.timedelta(days=365)
    sessions = StudySession.objects.filter(user=request.user, date__gte=one_year_ago).values('date__date').annotate(
        duration=Sum('duration_seconds')
    )
    # Create dict mapping 'YYYY-MM-DD' to duration in minutes
    calendar_data = {
        s['date__date'].strftime('%Y-%m-%d'): s['duration'] // 60
        for s in sessions
    }

    # 4. "En el atril" -- último proyecto practicado, para el panel de bienvenida
    #    (rediseño "Gabinete de estudio"). MusicalProject ya trackea compás/tempo
    #    actuales (mismo dato que usa proyecto_detail.html) -- no hace falta un
    #    modelo nuevo, solo tomar el más reciente por last_practice.
    from .models import MusicalProject
    atril = MusicalProject.objects.filter(user=request.user).select_related('sheet_music').order_by('-last_practice').first()

    # 5. Barras de habilidad del panel -- reusa get_user_progress() (ya calcula
    #    accuracy por Game) y toma 3 juegos representativos de las tres familias
    #    del entrenador (lectura / oído / ritmo), en vez de inventar una métrica
    #    nueva.
    habilidades_slugs = ['lectura-musical', 'intervalos-auditivos', 'tiempos-fuertes-debiles']
    progreso_por_slug = {p['game'].slug: p for p in get_user_progress(request.user)}
    habilidades = [progreso_por_slug[slug] for slug in habilidades_slugs if slug in progreso_por_slug]

    # 6. Destacado de Cursos junto al de Director de Estudio en el panel.
    # Mismo filtro (ninguno por idioma) que cursos_list -- ver el comentario ahí:
    # el contenido bilingüe vive dentro de cada bloque, no en Cursos separados.
    from .models import Curso
    cursos_disponibles_count = Curso.objects.filter(activo=True).count()

    context = {
        'profile': profile,
        'today_goals': today_goals,
        'recommendations': recommendations,
        'weekly_summary': weekly_summary,
        'calendar_data': json.dumps(calendar_data),
        'atril': atril,
        'habilidades': habilidades,
        'cursos_disponibles_count': cursos_disponibles_count,
    }
    return render(request, 'trainer/dashboard.html', context)

@login_required
def perfil(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    achievements = Achievement.objects.all()
    unlocked_slugs = UserAchievement.objects.filter(user=request.user).values_list('achievement__slug', flat=True)
    
    all_achievements = []
    for a in achievements:
        all_achievements.append({
            'obj': a,
            'unlocked': a.slug in unlocked_slugs
        })
        
    scores = Score.objects.filter(user=request.user)
    total_ans = sum(s.total_answers for s in scores)
    total_corr = sum(s.correct_answers for s in scores)
    global_acc = round((total_corr / total_ans) * 100) if total_ans > 0 else 0
    
    # Study Sessions
    study_sessions = StudySession.objects.filter(user=request.user)
    total_study_seconds = sum(s.duration_seconds for s in study_sessions)
    total_study_minutes = total_study_seconds // 60

    # Goals
    today = timezone.now().date()
    today_goals = UserDailyGoal.objects.filter(user=request.user, date=today)
    
    context = {
        'profile': profile,
        'achievements': all_achievements,
        'global_acc': global_acc,
        'total_answers': total_ans,
        'total_study_minutes': total_study_minutes,
        'today_goals': today_goals,
    }
    return render(request, 'trainer/perfil.html', context)

@login_required
def entrenador_index(request):
    progress = get_user_progress(request.user)
    return render(request, 'trainer/entrenador_index.html', {'progress': progress})

@login_required
def trainer_notas(request):
    game = get_object_or_404(Game, slug='notas')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    context = {'game': game, 'score': score}
    return render(request, 'trainer/trainer_notas.html', context)

@login_required
def trainer_intervalos(request):
    game = get_object_or_404(Game, slug='intervalos')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    context = {'game': game, 'score': score}
    return render(request, 'trainer/trainer_intervalos.html', context)

def parse_interval_size(presented_question):
    if '-' not in presented_question:
        return presented_question
    try:
        n1, n2 = presented_question.split('-')
        scale = ['c', 'd', 'e', 'f', 'g', 'a', 'b']
        c1, oct1 = n1.split('/')
        c2, oct2 = n2.split('/')
        idx1 = scale.index(c1) + int(oct1) * 7
        idx2 = scale.index(c2) + int(oct2) * 7
        dist = abs(idx2 - idx1) + 1
        interval_names = {
            2: "Segunda", 3: "Tercera", 4: "Cuarta", 
            5: "Quinta", 6: "Sexta", 7: "Séptima", 8: "Octava"
        }
        return interval_names.get(dist, f"{dist}a")
    except Exception:
        return presented_question

def get_game_stats(user, game):
    attempts = Attempt.objects.filter(user=user, game=game)
    total_answers = attempts.count()
    correct_answers = attempts.filter(is_correct=True).count()
    incorrect_answers = total_answers - correct_answers
    
    avg_time_ms = attempts.aggregate(Avg('response_time_ms'))['response_time_ms__avg']
    avg_time = round(avg_time_ms / 1000, 2) if avg_time_ms else 0
    
    stats = {}
    for attempt in attempts:
        q_name = parse_interval_size(attempt.presented_question)
        if q_name not in stats:
            stats[q_name] = {'total': 0, 'correct': 0}
        stats[q_name]['total'] += 1
        if attempt.is_correct:
            stats[q_name]['correct'] += 1
            
    hardest = []
    for q_name, s in stats.items():
        total = s['total']
        correct = s['correct']
        incorrect = total - correct
        accuracy = (correct / total) * 100 if total > 0 else 0
        if total >= 3:
            hardest.append({
                'interval': q_name,
                'accuracy': round(accuracy),
                'incorrect': incorrect
            })
            
    hardest = sorted(hardest, key=lambda x: (x['accuracy'], -x['incorrect']))[:3]
    
    return {
        'incorrect_answers': incorrect_answers,
        'avg_time': avg_time,
        'hardest': hardest
    }

@login_required
def trainer_intervalos_auditivos(request):
    game = get_object_or_404(Game, slug='intervalos-auditivos')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    stats_data = get_game_stats(request.user, game)
    
    context = {
        'game': game,
        'score': score,
        'incorrect_answers': stats_data['incorrect_answers'],
        'avg_time': stats_data['avg_time'],
        'hardest': stats_data['hardest'],
    }
    return render(request, 'trainer/trainer_intervalos_auditivos.html', context)

@login_required
def trainer_dictado_melodico(request):
    game = get_object_or_404(Game, slug='dictado-melodico')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    stats_data = get_game_stats(request.user, game)
    
    context = {
        'game': game,
        'score': score,
        'incorrect_answers': stats_data['incorrect_answers'],
        'avg_time': stats_data['avg_time'],
        'hardest': stats_data['hardest'],
    }
    return render(request, 'trainer/trainer_dictado_melodico.html', context)

@login_required
def trainer_lectura_musical(request):
    game = get_object_or_404(Game, slug='lectura-musical')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    stats_data = get_game_stats(request.user, game)
    
    pieces = Piece.objects.all().order_by('difficulty')
    level = request.GET.get('level', 1)
    try:
        level = int(level)
    except ValueError:
        level = 1
        
    piece = pieces.filter(difficulty=level).first()
    if not piece:
        piece = pieces.first()
    
    context = {
        'game': game,
        'score': score,
        'incorrect_answers': stats_data['incorrect_answers'],
        'avg_time': stats_data['avg_time'],
        'hardest': stats_data['hardest'],
        'pieces': pieces,
        'piece': piece,
        'current_level': level
    }
    return render(request, 'trainer/trainer_lectura_musical.html', context)

@login_required
def trainer_tiempos_fuertes_debiles(request):
    game = get_object_or_404(Game, slug='tiempos-fuertes-debiles')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    stats_data = get_game_stats(request.user, game)
    
    context = {
        'game': game,
        'score': score,
        'incorrect_answers': stats_data['incorrect_answers'],
        'avg_time': stats_data['avg_time']
    }
    return render(request, 'trainer/trainer_tiempos_fuertes.html', context)

@login_required
def trainer_sincopas_contratiempos(request):
    game = get_object_or_404(Game, slug='sincopas-contratiempos')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    stats_data = get_game_stats(request.user, game)
    
    context = {
        'game': game,
        'score': score,
        'incorrect_answers': stats_data['incorrect_answers'],
        'avg_time': stats_data['avg_time']
    }
    return render(request, 'trainer/trainer_sincopas.html', context)

@login_required
def trainer_reconocimiento_acordes(request):
    game = get_object_or_404(Game, slug='reconocimiento-acordes')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    stats_data = get_game_stats(request.user, game)
    
    context = {
        'game': game,
        'score': score,
        'incorrect_answers': stats_data['incorrect_answers'],
        'avg_time': stats_data['avg_time']
    }
    return render(request, 'trainer/trainer_acordes.html', context)

@login_required
def trainer_analisis_progresiones(request):
    game = get_object_or_404(Game, slug='analisis-progresiones')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    stats_data = get_game_stats(request.user, game)
    
    context = {
        'game': game,
        'score': score,
        'incorrect_answers': stats_data['incorrect_answers'],
        'avg_time': stats_data['avg_time']
    }
    return render(request, 'trainer/trainer_progresiones.html', context)

import music21

ORQUESTACION_TOOL = {
    "name": "reportar_analisis_orquestal",
    "description": "Registra el análisis de orquestación estructurado por bloques de compases de una partitura.",
    "input_schema": {
        "type": "object",
        "properties": {
            "resumen_general": {
                "type": "string",
                "description": "Resumen general del estilo, textura y distribución orquestal de la obra completa, en prosa exclusivamente. No incluyas datos estructurados, comillas escapadas, ni una copia o fragmento del contenido de bloques o resumen_por_instrumento — cada campo del schema es independiente, no dupliques información entre ellos."
            },
            "bloques": {
                "type": "array",
                "description": "Bloques secuenciales de compases que cubren toda la extensión de la obra.",
                "items": {
                    "type": "object",
                    "properties": {
                        "rango_compases": {
                            "type": "string",
                            "description": "Rango de compases que cubre este bloque (ej. '1-8')."
                        },
                        "analisis_cuerdas": {
                            "type": "string",
                            "description": "Análisis del plano de cuerdas en este bloque: registro, densidad, articulación."
                        },
                        "analisis_maderas": {
                            "type": "string",
                            "description": "Análisis del plano de maderas en este bloque."
                        },
                        "analisis_metales_percusion": {
                            "type": "string",
                            "description": "Análisis de metales y percusión en este bloque, si aplica."
                        },
                        "analisis_balance_y_fango": {
                            "type": "string",
                            "description": "Análisis del balance general, densidad armónica e interacción de tutti; dónde aparece empastamiento o fango tímbrico."
                        },
                        "solucion_prosa": {
                            "type": "string",
                            "description": "Solución orquestal en prosa, integrando los hallazgos del bloque."
                        },
                        "ediciones_sugeridas": {
                            "type": "array",
                            "description": "Instrucciones cortas de edición concretas para este bloque.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "compases": {"type": "string", "description": "Compás o rango de compases al que aplica la edición."},
                                    "parte": {"type": "string", "description": "Nombre exacto del instrumento tal como aparece en la partitura (partName de music21) — no traducir ni parafrasear, debe poder buscarse literalmente en el archivo original."},
                                    "accion": {"type": "string", "description": "Acción de edición corta (ej. 'transponer', 'redistribuir voz', 'ajustar dinámica')."},
                                    "detalle": {"type": "string", "description": "Detalle concreto de la acción, en prosa."},
                                    "compas_desde": {"type": "integer", "description": "Primer compás (inclusive, número entero) al que aplica la edición."},
                                    "compas_hasta": {"type": "integer", "description": "Último compás (inclusive, número entero) al que aplica la edición."},
                                    "accion_tipo": {
                                        "type": ["string", "null"],
                                        "enum": ["transponer_octava", "silenciar", None],
                                        "description": "Tipo de acción mecánicamente ejecutable sobre la partitura original, o null si la sugerencia no encaja en ninguna de las dos (ej. 'redistribuir voz', 'agregar contramelodía') — esas quedan solo con el texto de detalle, sin pentagrama comparado."
                                    },
                                    "direccion": {
                                        "type": ["string", "null"],
                                        "enum": ["arriba", "abajo", None],
                                        "description": "Solo si accion_tipo es 'transponer_octava': dirección de la transposición. Null en cualquier otro caso."
                                    }
                                },
                                "required": ["compases", "parte", "accion", "detalle", "compas_desde", "compas_hasta", "accion_tipo", "direccion"],
                                "additionalProperties": False
                            }
                        },
                        "duplicaciones_citadas": {
                            "type": "array",
                            "description": "Cada entrada respalda una afirmación de duplicación/doblaje/unísono/octava hecha con la palabra 'verificado' (o variante) en el texto de este bloque. Tiene que coincidir con una entrada real de duplicaciones_verificadas para ese rango. Si el bloque no afirma ninguna duplicación como verificada, este array queda vacío.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "parte_a": {"type": "string", "description": "Nombre exacto de la primera parte, igual que en duplicaciones_verificadas."},
                                    "parte_b": {"type": "string", "description": "Nombre exacto de la segunda parte, igual que en duplicaciones_verificadas."},
                                    "compas_desde": {"type": "integer"},
                                    "compas_hasta": {"type": "integer"},
                                    "tipo": {"type": "string", "enum": ["unísono", "octava", "intervalo_fijo"]}
                                },
                                "required": ["parte_a", "parte_b", "compas_desde", "compas_hasta", "tipo"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": [
                        "rango_compases",
                        "analisis_cuerdas",
                        "analisis_maderas",
                        "analisis_metales_percusion",
                        "analisis_balance_y_fango",
                        "solucion_prosa",
                        "ediciones_sugeridas",
                        "duplicaciones_citadas"
                    ],
                    "additionalProperties": False
                }
            },
            "resumen_por_instrumento": {
                "type": "array",
                "description": "Traducción a prosa de estadisticas_por_instrumento — no inventar ni recalcular números, solo redactar.",
                "items": {
                    "type": "object",
                    "properties": {
                        "instrumento": {"type": "string", "description": "Nombre del instrumento/parte."},
                        "descripcion": {"type": "string", "description": "Frase corta basada únicamente en los números ya calculados para ese instrumento (ámbito, notas totales, compases de silencio, clase de altura más frecuente)."}
                    },
                    "required": ["instrumento", "descripcion"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["resumen_general", "bloques", "resumen_por_instrumento"],
        "additionalProperties": False
    }
}

NOMBRES_SOLFEO = {'C': 'Do', 'D': 'Re', 'E': 'Mi', 'F': 'Fa', 'G': 'Sol', 'A': 'La', 'B': 'Si'}

def _a_solfeo(nombre_pitch):
    """Convierte un nombre de music21 (ej. 'C#4', 'B-3') a la notación de nota que
    corresponde al idioma activo de la request: solfeo español ('Do#4', 'Si-3') o,
    en inglés, el nombre de letra sin cambios (ya es lo que un usuario angloparlante
    espera ver, mismo criterio que la notación C/D/E/F/G/A/B ya usada en los Trainings)."""
    from django.utils.translation import get_language
    if get_language() == 'en':
        return nombre_pitch
    letra = nombre_pitch[0]
    resto = nombre_pitch[1:]
    return NOMBRES_SOLFEO.get(letra, letra) + resto

def calcular_estadisticas_parte(part):
    """
    Calcula de forma determinística (sin IA) estadísticas objetivas de una parte:
    ámbito realmente tocado, total de notas, compases de silencio total, y la
    clase de altura más frecuente.
    """
    all_pitches = []
    total_notas = 0
    for element in part.recurse().notes:
        if isinstance(element, music21.note.Note):
            all_pitches.append(element.pitch)
            total_notas += 1
        elif isinstance(element, music21.chord.Chord):
            all_pitches.extend(element.pitches)
            total_notas += len(element.pitches)

    measures = part.getElementsByClass(music21.stream.Measure)
    compases_silencio = sum(1 for m in measures if len(m.recurse().notes) == 0)

    from django.utils.translation import gettext as _

    if all_pitches:
        pitch_min = min(all_pitches, key=lambda p: p.ps)
        pitch_max = max(all_pitches, key=lambda p: p.ps)
        ambito = _("%(min)s a %(max)s") % {'min': _a_solfeo(pitch_min.nameWithOctave), 'max': _a_solfeo(pitch_max.nameWithOctave)}
        ambito_min_ps = pitch_min.ps
        ambito_max_ps = pitch_max.ps
        clase_mas_frecuente = Counter(p.name for p in all_pitches).most_common(1)[0][0]
        nota_mas_frecuente = _a_solfeo(clase_mas_frecuente)
    else:
        ambito = _("Sin notas")
        ambito_min_ps = None
        ambito_max_ps = None
        nota_mas_frecuente = "N/A"

    return {
        'ambito': ambito,
        'ambito_min_ps': ambito_min_ps,
        'ambito_max_ps': ambito_max_ps,
        'total_notas': total_notas,
        'compases_silencio': compases_silencio,
        'nota_mas_frecuente': nota_mas_frecuente,
    }


# Rangos prácticos/cómodos de referencia por instrumento (no el extremo teórico),
# en pitch escrito (igual que estadisticas_por_instrumento, sin transponer a concert pitch).
# Son aproximados y pensados para ajustarse con el tiempo, no una fuente normativa única.
RANGOS_COMODOS = {
    'Flautín': ('D5', 'C8'),
    'Flauta': ('C4', 'C7'),
    'Corno Inglés': ('B3', 'C6'),
    'Oboe': ('Bb3', 'F6'),
    'Clarinete Bajo': ('D3', 'G5'),
    'Clarinete': ('E3', 'C6'),
    'Contrafagot': ('Bb0', 'C4'),
    'Fagot': ('Bb1', 'D5'),
    'Corno': ('F2', 'C6'),
    'Trompeta': ('F#3', 'C6'),
    'Trombón Bajo': ('Bb1', 'F4'),
    'Trombón': ('E2', 'Bb4'),
    'Tuba': ('D1', 'F4'),
    'Violín': ('G3', 'C7'),
    'Viola': ('C3', 'E6'),
    'Violonchelo': ('C2', 'C6'),
    'Contrabajo': ('C1', 'G4'),
    'Arpa': ('C1', 'G7'),
}

# Orden deliberado: las entradas más específicas van antes que las genéricas que
# las contienen como substring (ej. 'contrafagot' antes que 'fagot', 'trombón bajo'
# antes que 'trombón'), para que el primer match gane siempre correctamente.
SINONIMOS_INSTRUMENTOS = [
    (('piccolo', 'flautín', 'flautin'), 'Flautín'),
    (('corno inglés', 'corno ingles', 'english horn', 'cor anglais'), 'Corno Inglés'),
    (('oboe',), 'Oboe'),
    (('clarinete bajo', 'bass clarinet'), 'Clarinete Bajo'),
    (('clarinet', 'clarinete'), 'Clarinete'),
    (('contrafagot', 'contrabassoon'), 'Contrafagot'),
    (('fagot', 'bassoon'), 'Fagot'),
    (('horn', 'corno', 'trompa'), 'Corno'),
    (('trumpet', 'trompeta'), 'Trompeta'),
    (('trombón bajo', 'trombon bajo', 'bass trombone'), 'Trombón Bajo'),
    (('trombone', 'trombón', 'trombon'), 'Trombón'),
    (('tuba',), 'Tuba'),
    (('violin', 'violín'), 'Violín'),
    (('viola',), 'Viola'),
    (('violoncello', 'violonchelo', 'cello'), 'Violonchelo'),
    (('contrabass', 'contrabajo', 'double bass'), 'Contrabajo'),
    (('harp', 'arpa'), 'Arpa'),
    (('flute', 'flauta', 'fl.'), 'Flauta'),
]


def _buscar_rango_comodo(part_name):
    nombre_norm = (part_name or '').lower()
    for keywords, canonico in SINONIMOS_INSTRUMENTOS:
        if any(kw in nombre_norm for kw in keywords):
            return RANGOS_COMODOS[canonico]
    return None


def _serializar_rangos_comodos():
    """
    RANGOS_COMODOS en formato JSON-friendly (con .ps ya calculado) para el ejercicio
    de orquestación en el frontend — ahí no hay ningún parser de nombres de nota en
    JS, así que la altura en semitonos (.ps) se calcula acá, la única fuente de
    verdad para pitch-math en todo el proyecto.
    """
    return {
        nombre: {
            'min': lo, 'max': hi,
            'min_ps': music21.pitch.Pitch(lo).ps, 'max_ps': music21.pitch.Pitch(hi).ps,
        }
        for nombre, (lo, hi) in RANGOS_COMODOS.items()
    }


def evaluar_viabilidad_instrumental(part_name, part):
    """
    Compara (con music21, sin IA) el ámbito realmente tocado por una parte contra un
    rango cómodo/práctico de referencia. Si el instrumento no matchea ninguna entrada
    conocida de RANGOS_COMODOS, no genera alertas — mejor ninguna alerta que una mal
    atribuida a un instrumento equivocado.
    """
    from django.utils.translation import gettext as _

    rango = _buscar_rango_comodo(part_name)
    if rango is None:
        return []

    comodo_min = music21.pitch.Pitch(rango[0]).ps
    comodo_max = music21.pitch.Pitch(rango[1]).ps
    margen = 2  # semitonos de margen para considerar que una nota "roza" el límite
    nombre_min = _a_solfeo(music21.pitch.Pitch(rango[0]).nameWithOctave)
    nombre_max = _a_solfeo(music21.pitch.Pitch(rango[1]).nameWithOctave)

    candidatas_agudas = []
    candidatas_graves = []
    for m in part.getElementsByClass(music21.stream.Measure):
        for element in m.recurse().notes:
            pitches = element.pitches if isinstance(element, music21.chord.Chord) else [element.pitch]
            for p in pitches:
                if p.ps > comodo_max - margen:
                    candidatas_agudas.append((p.ps, m.number, p))
                if p.ps < comodo_min + margen:
                    candidatas_graves.append((p.ps, m.number, p))

    alertas = []
    if candidatas_agudas:
        ps, compas, p = max(candidatas_agudas, key=lambda t: t[0])
        severidad = 'excede' if ps > comodo_max else 'roza'
        nota = _a_solfeo(p.nameWithOctave)
        if severidad == 'excede':
            mensaje = _("Atención: %(instrumento)s excede el registro agudo cómodo (%(nombre_min)s a %(nombre_max)s) alcanzando %(nota)s en el compás %(compas)s.")
        else:
            mensaje = _("Atención: %(instrumento)s roza el registro agudo cómodo (%(nombre_min)s a %(nombre_max)s) alcanzando %(nota)s en el compás %(compas)s.")
        alertas.append({
            'instrumento': part_name,
            'compas': compas,
            'nota': nota,
            'severidad': severidad,
            'mensaje': mensaje % {
                'instrumento': part_name, 'nombre_min': nombre_min, 'nombre_max': nombre_max,
                'nota': nota, 'compas': compas,
            }
        })
    if candidatas_graves:
        ps, compas, p = min(candidatas_graves, key=lambda t: t[0])
        severidad = 'excede' if ps < comodo_min else 'roza'
        nota = _a_solfeo(p.nameWithOctave)
        if severidad == 'excede':
            mensaje = _("Atención: %(instrumento)s excede el registro grave cómodo (%(nombre_min)s a %(nombre_max)s) descendiendo a %(nota)s en el compás %(compas)s.")
        else:
            mensaje = _("Atención: %(instrumento)s roza el registro grave cómodo (%(nombre_min)s a %(nombre_max)s) descendiendo a %(nota)s en el compás %(compas)s.")
        alertas.append({
            'instrumento': part_name,
            'compas': compas,
            'nota': nota,
            'severidad': severidad,
            'mensaje': mensaje % {
                'instrumento': part_name, 'nombre_min': nombre_min, 'nombre_max': nombre_max,
                'nota': nota, 'compas': compas,
            }
        })
    return alertas


def calcular_densidad_por_compas(parts):
    """
    Para cada compás de la obra, cuenta cuántos instrumentos tienen al menos una nota
    sonando (silencios no cuentan). Determinístico con music21, sin IA.
    """
    todos_los_compases = set()
    activos_por_compas = {}

    for part in parts:
        for m in part.getElementsByClass(music21.stream.Measure):
            todos_los_compases.add(m.number)
            if len(m.recurse().notes) > 0:
                activos_por_compas[m.number] = activos_por_compas.get(m.number, 0) + 1

    total_instrumentos = len(parts)
    return [
        {'compas': n, 'instrumentos_activos': activos_por_compas.get(n, 0), 'total_instrumentos': total_instrumentos}
        for n in sorted(todos_los_compases)
    ]


def _eventos_sonantes_por_compas(part):
    """
    Para una parte, arma {compás: [altura1, altura2, ...]} — la secuencia de alturas (en
    .ps, semitonos) realmente sonando en cada compás, en orden de ataque, ignorando
    silencios. En acordes se usa la nota más aguda como altura representativa del ataque.

    La percusión sin altura definida (music21.note.Unpitched — caja, bombo, platillo, etc.)
    se ignora igual que los silencios: no tiene .pitch, y no puede compararse por altura
    con ninguna otra parte, así que simplemente no genera evento sonante. Esa ausencia total
    de eventos ya alcanza para que ningún par con esa parte produzca una entrada de
    duplicación, sin necesidad de lógica especial adicional en la comparación.
    """
    resultado = {}
    for m in part.getElementsByClass(music21.stream.Measure):
        eventos = []
        for el in m.recurse().notes:
            if isinstance(el, music21.chord.Chord):
                if el.pitches:
                    eventos.append(max(el.pitches, key=lambda p: p.ps).ps)
            elif isinstance(el, music21.note.Note):
                eventos.append(el.pitch.ps)
            # music21.note.Unpitched (percusión sin altura) y cualquier otro tipo
            # inesperado se ignoran deliberadamente: no aportan una altura comparable.
        if eventos:
            resultado[m.number] = eventos
    return resultado


def _clasificar_relacion(eventos_a, eventos_b):
    """
    Compara dos secuencias de alturas (mismo compás, un par de partes) alineadas por
    orden de ataque. Si la cantidad de ataques difiere, no puede ser doblaje real (el
    doblaje implica el mismo ritmo, no solo las mismas alturas) — se clasifica directo
    como 'sin_relacion' sin intentar un matcheo parcial.
    """
    if len(eventos_a) != len(eventos_b):
        return 'sin_relacion'
    intervalos = [b - a for a, b in zip(eventos_a, eventos_b)]
    primero = intervalos[0]
    if not all(abs(iv - primero) < 0.001 for iv in intervalos):
        return 'sin_relacion'
    if abs(primero) < 0.001:
        return 'unísono'
    if abs(primero % 12) < 0.001:
        return 'octava'
    return 'intervalo_fijo'


def detectar_duplicaciones_verificadas(parts):
    """
    Calcula (con music21, sin IA) qué pares de partes comparten literalmente las mismas
    alturas (o las mismas a distancia de octava, o a intervalo fijo) en cada compás de la
    obra, agrupando en rangos contiguos de compases donde la relación se mantiene. Es un
    reemplazo determinístico de la detección de doblaje que antes dependía de la lectura
    del modelo de IA — ahora ese cálculo ya viene hecho y verificado.
    """
    parts = list(parts)
    eventos_por_parte = [_eventos_sonantes_por_compas(p) for p in parts]
    nombres = [p.partName or f"Parte {i+1}" for i, p in enumerate(parts)]

    duplicaciones = []
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            eventos_a, eventos_b = eventos_por_parte[i], eventos_por_parte[j]
            compases_comunes = sorted(set(eventos_a.keys()) & set(eventos_b.keys()))

            tipo_actual = None
            desde = None
            compas_previo = None
            for c in compases_comunes:
                tipo = _clasificar_relacion(eventos_a[c], eventos_b[c])

                if tipo == 'sin_relacion':
                    if tipo_actual:
                        duplicaciones.append({
                            'parte_a': nombres[i], 'parte_b': nombres[j],
                            'compas_desde': desde, 'compas_hasta': compas_previo, 'tipo': tipo_actual,
                        })
                    tipo_actual = None
                    compas_previo = c
                    continue

                if tipo == tipo_actual and compas_previo is not None and c == compas_previo + 1:
                    compas_previo = c
                    continue

                if tipo_actual:
                    duplicaciones.append({
                        'parte_a': nombres[i], 'parte_b': nombres[j],
                        'compas_desde': desde, 'compas_hasta': compas_previo, 'tipo': tipo_actual,
                    })
                tipo_actual = tipo
                desde = c
                compas_previo = c

            if tipo_actual:
                duplicaciones.append({
                    'parte_a': nombres[i], 'parte_b': nombres[j],
                    'compas_desde': desde, 'compas_hasta': compas_previo, 'tipo': tipo_actual,
                })

    return duplicaciones


CONTEXTO_COMPASES = 2  # compases de contexto a mostrar antes/después del rango editado

# Mismo valor que --editado en templates/base.html (rediseño "Sala de conciertos") --
# tiene que leerse como "nota modificada", no como un rojo de error genérico. No hay
# forma de que Python lea la variable CSS, así que el valor se mantiene sincronizado
# a mano entre los dos lugares.
COLOR_NOTA_EDITADA = '#c9433f'


def generar_fragmento_comparado(part, compas_desde, compas_hasta, accion_tipo, direccion, min_compas, max_compas):
    """
    Extrae (con music21, sin IA) el fragmento de compases [compas_desde, compas_hasta] de una
    parte ya parseada, ampliado con CONTEXTO_COMPASES compases antes y después (recortado a los
    límites reales de la parte), genera una copia con la transformación mecánica aplicada
    únicamente en el rango original de la edición — el contexto queda idéntico en ambos
    pentagramas — y devuelve ambos fragmentos como MusicXML (string) para que el frontend los
    renderice con OSMD.

    Solo 'transponer_octava' genera pentagrama comparado: comparar contra compases de silencio
    (accion_tipo='silenciar') no aporta nada visualmente, así que esa acción quedó fuera de este
    camino — sigue siendo una sugerencia válida, pero solo como texto en prosa.
    """
    desde_ancho = max(compas_desde - CONTEXTO_COMPASES, min_compas)
    hasta_ancho = min(compas_hasta + CONTEXTO_COMPASES, max_compas)

    fragmento_original = part.measures(desde_ancho, hasta_ancho)
    fragmento_editado = copy.deepcopy(fragmento_original)

    semitonos = 12 if direccion == 'arriba' else -12
    for m in fragmento_editado.getElementsByClass(music21.stream.Measure):
        if compas_desde <= m.number <= compas_hasta:
            m.transpose(semitonos, inPlace=True)
            # Notas realmente tocadas por la edición, en rojo — los compases de contexto
            # quedan sin colorear porque no cambiaron.
            for elemento in m.recurse().notes:
                elemento.style.color = COLOR_NOTA_EDITADA

    exporter_original = music21.musicxml.m21ToXml.GeneralObjectExporter(fragmento_original)
    exporter_editado = music21.musicxml.m21ToXml.GeneralObjectExporter(fragmento_editado)
    return (
        exporter_original.parse().decode('utf-8'),
        exporter_editado.parse().decode('utf-8'),
        (desde_ancho, hasta_ancho),
    )

def _parsear_score_descifrado(score_file_field):
    """
    Lee un FileField (el Storage lo descifra automáticamente vía .open()) y lo parsea
    con music21 completamente en memoria, sin volcar el contenido descifrado a disco
    ni siquiera transitoriamente. Soporta los 3 formatos que acepta el analizador.
    """
    with score_file_field.open('rb') as f:
        contenido = f.read()

    extension = pathlib.Path(score_file_field.name).suffix.lower()

    if extension == '.mxl':
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            xml_bytes = None
            for nombre_interno in zf.namelist():
                if 'META-INF' in nombre_interno:
                    continue
                if pathlib.Path(nombre_interno).suffix.lower() not in ('.musicxml', '.xml', '.mxl'):
                    continue
                xml_bytes = zf.read(nombre_interno)
                break
        if xml_bytes is None:
            raise ValueError('No se encontró el XML dentro del archivo .mxl.')
        return music21.converter.parseData(xml_bytes.decode('utf-8'))

    if extension in ('.mid', '.midi'):
        return music21.converter.parseData(contenido, format='midi')

    return music21.converter.parseData(contenido.decode('utf-8'))


def comparar_versiones(anterior, nueva_parts):
    """
    Compara (con music21, sin IA) las ediciones sugeridas mecánicamente ejecutables del
    análisis anterior contra el estado real de la nueva versión, para detectar si el
    problema señalado parece resuelto. Las sugerencias de texto libre (accion_tipo null)
    no se verifican automáticamente — quedan marcadas como 'sin_verificar'.
    """
    anterior_data = anterior.analysis_data or {}
    resultado = []

    try:
        score_anterior = _parsear_score_descifrado(anterior.score_file)
        parts_anterior = {p.partName: p for p in score_anterior.parts}
    except Exception:
        parts_anterior = {}

    parts_nueva_por_nombre = {p.partName: p for p in nueva_parts}

    for bloque in anterior_data.get('bloques', []):
        for edicion in bloque.get('ediciones_sugeridas', []):
            accion_tipo = edicion.get('accion_tipo')
            parte = edicion.get('parte')
            compas_desde = edicion.get('compas_desde')
            compas_hasta = edicion.get('compas_hasta')

            item = {
                'parte': parte,
                'compases': edicion.get('compases'),
                'accion': edicion.get('accion'),
                'detalle': edicion.get('detalle'),
            }

            if accion_tipo not in ('transponer_octava', 'silenciar') or compas_desde is None or compas_hasta is None:
                item['estado'] = 'sin_verificar'
                item['motivo'] = 'Sugerencia de texto libre — no se puede verificar automáticamente.'
                resultado.append(item)
                continue

            part_nueva = parts_nueva_por_nombre.get(parte)
            if part_nueva is None:
                item['estado'] = 'sin_verificar'
                item['motivo'] = f"La parte '{parte}' no se encontró en la nueva versión."
                resultado.append(item)
                continue

            try:
                fragmento_nuevo = part_nueva.measures(compas_desde, compas_hasta)
            except Exception:
                item['estado'] = 'sin_verificar'
                item['motivo'] = 'No se pudo extraer ese rango de compases en la nueva versión.'
                resultado.append(item)
                continue

            if accion_tipo == 'silenciar':
                sigue_sonando = len(fragmento_nuevo.recurse().notes) > 0
                item['estado'] = 'no_resuelto' if sigue_sonando else 'resuelto'
                item['motivo'] = (
                    'Esa parte sigue sonando en ese rango.' if sigue_sonando
                    else 'Esa parte ya no suena en ese rango — coincide con lo sugerido.'
                )
                resultado.append(item)
                continue

            # transponer_octava: comparamos el registro promedio de ese rango entre versiones
            direccion = edicion.get('direccion')
            pitches_nuevos = [
                p.ps for n in fragmento_nuevo.recurse().notes
                for p in (n.pitches if isinstance(n, music21.chord.Chord) else [n.pitch])
            ]

            part_anterior = parts_anterior.get(parte)
            pitches_anteriores = []
            if part_anterior is not None:
                try:
                    fragmento_anterior = part_anterior.measures(compas_desde, compas_hasta)
                    pitches_anteriores = [
                        p.ps for n in fragmento_anterior.recurse().notes
                        for p in (n.pitches if isinstance(n, music21.chord.Chord) else [n.pitch])
                    ]
                except Exception:
                    pitches_anteriores = []

            if not pitches_nuevos or not pitches_anteriores:
                item['estado'] = 'sin_verificar'
                item['motivo'] = 'No hay suficientes notas para comparar el registro en ese rango.'
                resultado.append(item)
                continue

            promedio_anterior = sum(pitches_anteriores) / len(pitches_anteriores)
            promedio_nuevo = sum(pitches_nuevos) / len(pitches_nuevos)
            diferencia = promedio_nuevo - promedio_anterior
            umbral = 6  # semitonos — evita falsos positivos por ajustes menores

            if direccion == 'arriba' and diferencia >= umbral:
                item['estado'] = 'resuelto'
                item['motivo'] = f"El registro subió ~{round(diferencia)} semitonos en ese rango."
            elif direccion == 'abajo' and diferencia <= -umbral:
                item['estado'] = 'resuelto'
                item['motivo'] = f"El registro bajó ~{round(abs(diferencia))} semitonos en ese rango."
            else:
                item['estado'] = 'no_resuelto'
                item['motivo'] = 'El registro en ese rango no parece haberse movido lo suficiente en la dirección sugerida.'
            resultado.append(item)

    return resultado


CLAVES_SCHEMA_FUGABLES = ('resumen_por_instrumento', 'bloques')


def _limpiar_fuga_json_en_resumen(resumen_general):
    """
    Defensa contra un comportamiento real del modelo confirmado en producción: en
    respuestas grandes, Claude a veces escribe (con comillas escapadas, JSON válido) una
    copia de otro campo del schema dentro del string de resumen_general. Si detectamos
    ese patrón — una subcadena que empieza con una clave conocida del schema y que,
    aislada, parsea como JSON válido con esa clave — cortamos el texto ahí, antes de la
    fuga, en vez de guardar el JSON crudo en el reporte final. En el peor caso el usuario
    ve un resumen un poco más corto de lo esperado; nunca ve datos sin procesar.
    """
    if not isinstance(resumen_general, str):
        return resumen_general

    decoder = json.JSONDecoder()
    mejor_corte = None
    for clave in CLAVES_SCHEMA_FUGABLES:
        patron = f'"{clave}"'
        idx = resumen_general.find(patron)
        while idx != -1:
            fragmento = resumen_general[idx:]
            try:
                # raw_decode (a diferencia de json.loads) parsea un único valor JSON
                # completo y tolera basura sobrante después. Le agregamos margen generoso
                # de llaves de cierre: si el fragmento ya trae su propio cierre (como la
                # fuga real observada en producción, que incluye una llave extra al
                # final) se detiene ahí solo y el resto queda como sobrante ignorado; si
                # no lo trae, usa una de las nuestras para cerrar correctamente.
                parseado, _ = decoder.raw_decode("{" + fragmento + "}}}")
                if isinstance(parseado, dict) and clave in parseado:
                    corte = idx
                    # La fuga suele venir precedida por la comilla/coma de cierre del
                    # string original — la recortamos también para que el texto quede limpio.
                    while corte > 0 and resumen_general[corte - 1] in '",':
                        corte -= 1
                    if mejor_corte is None or corte < mejor_corte:
                        mejor_corte = corte
                    break
            except (json.JSONDecodeError, ValueError):
                pass
            idx = resumen_general.find(patron, idx + 1)

    if mejor_corte is not None:
        return resumen_general[:mejor_corte].rstrip()
    return resumen_general


PATRON_VERIFICADO = re.compile(r'verificad[oa]s?', re.IGNORECASE)
CAMPOS_PROSA_BLOQUE = ('analisis_cuerdas', 'analisis_maderas', 'analisis_metales_percusion', 'analisis_balance_y_fango', 'solucion_prosa')
LIMITES_CLAUSULA = re.compile(r'[.,;]')
NEGACIONES_VERIFICADO = ('sin', 'no')


def _hay_negacion_cercana(texto, inicio_match):
    """
    Busca una negación ('sin'/'no' — cubre también 'sin que', 'no hay', 'no está') en
    toda la cláusula que contiene esta aparición de 'verificado'/'verificada',
    delimitada hacia atrás por el punto, coma o punto y coma más cercano (o el inicio
    del texto si no hay ninguno) — no una cantidad fija de palabras. La negación en
    español rige sobre la cláusula, no sobre N palabras, así que esto se ajusta mejor
    que una ventana arbitraria. Heurística, no un parser real: no captura negación al
    100%, solo reduce falsos positivos obvios (ej. "sin ser doblaje verificado").
    """
    antes = texto[:inicio_match]
    limites = [m.end() for m in LIMITES_CLAUSULA.finditer(antes)]
    clausula = antes[limites[-1]:] if limites else antes
    palabras = re.findall(r"\w+", clausula, re.UNICODE)
    return any(p.lower() in NEGACIONES_VERIFICADO for p in palabras)


def _auditar_citas_duplicaciones(bloques, duplicaciones_verificadas):
    """
    Auditoría determinística (sin IA, solo logging) de que cada uso de la palabra
    "verificado"/"verificada" en el texto de un bloque esté respaldado por una entrada
    real en duplicaciones_verificadas, citada en duplicaciones_citadas de ese mismo
    bloque. No modifica final_data ni bloquea nada — solo deja registro para juntar
    casos reales antes de decidir si hace falta rechazar/reintentar la respuesta.
    """
    for bloque in bloques or []:
        citas = bloque.get('duplicaciones_citadas') or []
        texto = ' '.join(bloque.get(c, '') or '' for c in CAMPOS_PROSA_BLOQUE)
        texto += ' ' + ' '.join(e.get('detalle', '') or '' for e in bloque.get('ediciones_sugeridas', []))
        usa_verificado = any(
            not _hay_negacion_cercana(texto, m.start())
            for m in PATRON_VERIFICADO.finditer(texto)
        )

        if usa_verificado and not citas:
            logger.warning(
                "auditoria_duplicaciones: bloque %r usa 'verificado'/'verificada' en el "
                "texto pero duplicaciones_citadas está vacío. Texto: %.300s",
                bloque.get('rango_compases'), texto,
            )
            continue

        for cita in citas:
            existe = any(
                {cita.get('parte_a'), cita.get('parte_b')} == {v['parte_a'], v['parte_b']}
                and cita.get('tipo') == v['tipo']
                and cita.get('compas_desde') is not None and cita.get('compas_hasta') is not None
                and cita['compas_desde'] <= v['compas_hasta']
                and cita['compas_hasta'] >= v['compas_desde']
                for v in duplicaciones_verificadas
            )
            if not existe:
                logger.warning(
                    "auditoria_duplicaciones: bloque %r citó una duplicación inventada: %r "
                    "— no coincide con ninguna entrada real de duplicaciones_verificadas.",
                    bloque.get('rango_compases'), cita,
                )


UMBRAL_PUNTAJE_CONFIRMACION = 800  # ver justificación en CLAUDE.md / historial de diseño — punto de partida, no calibrado
MOTIVO_CONFIRMACION_OBRA_GRANDE = (
    "Esta obra tiene una escala considerable — un análisis con este nivel de detalle "
    "va a consumir 2 créditos en lugar de 1. ¿Querés continuar?"
)
MOTIVO_CREDITOS_INSUFICIENTES_OBRA_GRANDE = (
    "Esta obra tiene una escala considerable — un análisis con este nivel de detalle "
    "necesita 2 créditos, y tu saldo actual no alcanza."
)


def _calcular_puntaje_obra(total_instrumentos, measures_data):
    """
    Puntaje determinístico usado para decidir si una obra es lo bastante grande
    como para pedir confirmación antes de gastar créditos: instrumentos × compases.
    Reutiliza measures_data ya calculado en el pipeline (no vuelve a recorrer music21)
    — total_compases es el máximo entre partes, no la suma, porque compases es una
    propiedad de la obra, no de cada instrumento por separado.
    """
    total_compases = max((len(v) for v in measures_data.values()), default=0)
    return total_instrumentos * total_compases


def _generar_analisis_orquestacion(analysis, version_de, creditos_a_cobrar, omitir_chequeo_tamano=False):
    """
    Generador NDJSON compartido por orquestador_analizar (primer intento, 1 crédito,
    puede terminar pidiendo confirmación si la obra es grande) y
    orquestador_analizar_confirmado (segundo paso ya confirmado por el usuario, 2
    créditos, omitir_chequeo_tamano=True salta directo a analizar). Factorizado a
    función de módulo (en vez de closure) porque ahora tiene dos puntos de entrada.
    """
    from .services import consumir_credito_analisis, consumir_creditos_analisis_multiple, CreditosInsuficientesError
    profile, _ = UserProfile.objects.get_or_create(user=analysis.user)

    # Heartbeat inicial: que la conexión tenga tráfico desde el primer instante,
    # antes incluso de arrancar el parseo con music21.
    yield json.dumps({"heartbeat": True}) + "\n"

    try:
        score = _parsear_score_descifrado(analysis.score_file)
        parts = score.parts
        instrument_names = [p.partName for p in parts if p.partName]

        try:
            key_sig = score.analyze('key')
            key_str = str(key_sig)
        except:
            key_str = "Unknown"

        time_sig = score.recurse().getElementsByClass(music21.meter.TimeSignature)
        ts = time_sig[0].ratioString if time_sig else "Unknown"

        tempos = score.recurse().getElementsByClass(music21.tempo.MetronomeMark)
        tempo = tempos[0].number if tempos else "Unknown"

        measures_data = {}
        estadisticas_por_instrumento = {}
        alertas_viabilidad = []
        for part in parts:
            part_name = part.partName or "Instrumento Desconocido"
            measures = part.getElementsByClass(music21.stream.Measure)

            part_data = []
            for m in list(measures):
                m_dict = {'number': m.number, 'notes': []}
                for element in m.recurse().notes:
                    if isinstance(element, music21.note.Note):
                        m_dict['notes'].append(f"{element.nameWithOctave} ({element.duration.type})")
                    elif isinstance(element, music21.chord.Chord):
                        chord_notes = "-".join([n.nameWithOctave for n in element.notes])
                        m_dict['notes'].append(f"[{chord_notes}] ({element.duration.type})")

                dynamics = m.recurse().getElementsByClass(music21.dynamics.Dynamic)
                for d in dynamics:
                    m_dict['notes'].append(f"Dinámica: {d.value}")

                part_data.append(m_dict)
            measures_data[part_name] = part_data
            estadisticas_por_instrumento[part_name] = calcular_estadisticas_parte(part)
            alertas_viabilidad.extend(evaluar_viabilidad_instrumental(part_name, part))

            # Heartbeat por instrumento: en una obra con muchas partes, el parseo en
            # sí puede tardar — esto evita huecos largos de silencio antes de llegar
            # siquiera a llamar a Claude.
            yield json.dumps({"heartbeat": True}) + "\n"

        # Puntaje de tamaño y chequeo de créditos, antes de gastar nada en la API.
        # Se calcula siempre (incluso en el camino ya confirmado) porque es gratis y
        # queda guardado para calibrar el umbral más adelante con datos reales.
        puntaje_obra = _calcular_puntaje_obra(len(parts), measures_data)
        analysis.puntaje_obra = puntaje_obra
        disponibles = profile.creditos_analisis + profile.creditos_bonus

        pide_confirmacion = (not omitir_chequeo_tamano) and (puntaje_obra > UMBRAL_PUNTAJE_CONFIRMACION)
        if pide_confirmacion:
            analysis.save(update_fields=['puntaje_obra'])
            if disponibles < 2:
                yield json.dumps({
                    'status': 'creditos_insuficientes',
                    'analysis_id': analysis.id,
                    'creditos_estimados': 2,
                    'creditos_disponibles': disponibles,
                    'motivo': MOTIVO_CREDITOS_INSUFICIENTES_OBRA_GRANDE,
                }) + "\n"
            else:
                yield json.dumps({
                    'status': 'confirmar',
                    'analysis_id': analysis.id,
                    'creditos_estimados': 2,
                    'motivo': MOTIVO_CONFIRMACION_OBRA_GRANDE,
                }) + "\n"
            return

        if disponibles < creditos_a_cobrar:
            analysis.save(update_fields=['puntaje_obra'])
            yield json.dumps({'status': 'creditos_insuficientes', 'analysis_id': analysis.id,
                               'creditos_estimados': creditos_a_cobrar, 'creditos_disponibles': disponibles,
                               'motivo': 'No había créditos suficientes para completar este análisis.'}) + "\n"
            return

        densidad_por_compas = calcular_densidad_por_compas(parts)
        duplicaciones_verificadas = detectar_duplicaciones_verificadas(parts)

        analysis_data = {
            'instruments': instrument_names,
            'key_signature': key_str,
            'time_signature': ts,
            'tempo': tempo,
            'measures_data': measures_data,
            'estadisticas_por_instrumento': estadisticas_por_instrumento,
            'duplicaciones_verificadas': duplicaciones_verificadas,
        }

        api_key = os.environ.get("ANTHROPIC_TEST_API_KEY")
        if api_key:
            client = anthropic.Anthropic(api_key=api_key)

            prompt = f"Analizá la siguiente estructura de datos musicales extraída de la partitura:\n{json.dumps(analysis_data, ensure_ascii=False)}"

            with client.messages.stream(
                model="claude-sonnet-5",
                max_tokens=48000,
                system=[
                    {
                        "type": "text",
                        "text": GUIA_ESTILO_ORQUESTAL,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[ORQUESTACION_TOOL],
                tool_choice={"type": "tool", "name": "reportar_analisis_orquestal"},
                messages=[
                    {"role": "user", "content": prompt}
                ],
            ) as stream:
                json_bruto_tool_use = ""
                for event in stream:
                    # El SDK expone, además del evento crudo, un evento "input_json" con
                    # el fragmento de texto tal cual llegó — lo acumulamos nosotros mismos
                    # para poder parsearlo con json.loads estricto al final, en vez de
                    # confiar en el snapshot que arma el SDK internamente (ver más abajo).
                    if event.type == "input_json":
                        json_bruto_tool_use += event.partial_json
                    # Heartbeat por cada evento del stream de Claude: con max_tokens=48000
                    # esto da tráfico constante durante todo el minuto y medio que puede
                    # tardar una obra grande.
                    yield json.dumps({"heartbeat": True}) + "\n"
                message = stream.get_final_message()

            # TEMPORAL: diagnóstico de un caso real donde el resultado llegó incompleto
            # (alertas_viabilidad presente pero bloques ausente) — confirmar si se corta
            # por max_tokens. Sacar una vez confirmado.
            logger.warning(
                "orquestador_analizar: stop_reason=%s, tokens_entrada=%s, tokens_salida=%s",
                message.stop_reason, message.usage.input_tokens, message.usage.output_tokens,
            )

            tool_use_block = next(
                (block for block in message.content if block.type == "tool_use"),
                None
            )
            if tool_use_block:
                try:
                    # El SDK parsea el JSON del tool_use con partial_mode=True (tolerante
                    # a datos incompletos) incluso para el resultado final — con
                    # respuestas muy grandes (max_tokens=48000) esto puede dejar el resto
                    # del JSON crudo pegado como texto dentro de un campo de string (bug
                    # real encontrado: resumen_general con ~1000 caracteres y después el
                    # resto del objeto sin parsear). Usamos json.loads estricto sobre el
                    # texto crudo que acumulamos nosotros mismos del stream, y solo caemos
                    # al snapshot del SDK si por algún motivo no es JSON válido.
                    final_data = json.loads(json_bruto_tool_use)
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(
                        "orquestador_analizar: json.loads estricto falló sobre "
                        "json_bruto_tool_use (%d caracteres): %s. Cayendo a "
                        "tool_use_block.input del SDK.",
                        len(json_bruto_tool_use), e,
                    )
                    final_data = tool_use_block.input

                # TEMPORAL: diagnóstico del bug de resumen_general con JSON pegado al
                # final. Confirma si el marcador ya estaba en el texto crudo del stream
                # (bug del lado del modelo/generación) o si aparece recién después de
                # nuestro parseo (bug de nuestro lado). Sacar una vez confirmado.
                marcador = '"resumen_por_instrumento"'
                resumen = final_data.get('resumen_general') if isinstance(final_data, dict) else None
                if isinstance(resumen, str) and marcador in resumen:
                    logger.warning(
                        "orquestador_analizar: BUG CONFIRMADO - resumen_general (%d "
                        "caracteres) contiene el marcador %r. ¿Presente también en "
                        "json_bruto_tool_use crudo (antes de parsear)?: %s",
                        len(resumen), marcador, marcador in json_bruto_tool_use,
                    )

                # Limpieza defensiva: pase lo que pase con el ajuste de prompt, ningún
                # usuario final debe ver una fuga de JSON crudo en su reporte.
                if isinstance(resumen, str):
                    resumen_limpio = _limpiar_fuga_json_en_resumen(resumen)
                    if resumen_limpio != resumen:
                        logger.warning(
                            "orquestador_analizar: se recortó una fuga de JSON dentro de "
                            "resumen_general (de %d a %d caracteres).",
                            len(resumen), len(resumen_limpio),
                        )
                        final_data['resumen_general'] = resumen_limpio

                if isinstance(final_data, dict):
                    _auditar_citas_duplicaciones(final_data.get('bloques'), duplicaciones_verificadas)

                final_data['estadisticas_por_instrumento'] = estadisticas_por_instrumento
                final_data['alertas_viabilidad'] = alertas_viabilidad
                final_data['densidad_por_compas'] = densidad_por_compas
                if version_de is not None:
                    final_data['comparacion_version_anterior'] = comparar_versiones(version_de, parts)

                try:
                    if creditos_a_cobrar == 1:
                        consumir_credito_analisis(profile)
                    else:
                        consumir_creditos_analisis_multiple(profile, creditos_a_cobrar)
                except CreditosInsuficientesError:
                    # Re-chequeo final por si el saldo cambió entre el aviso y esta
                    # confirmación (otra pestaña, por ejemplo). Ya se pagó el costo de la
                    # llamada a Claude, pero no se cobra ni se guarda como éxito.
                    final_data = {"error": "No había créditos suficientes para completar este análisis.", "raw_music_data": analysis_data}
                    analysis.analysis_data = final_data
                    analysis.save()
                    yield json.dumps({'status': 'error', 'message': 'No había créditos suficientes para completar este análisis.'}) + "\n"
                    return

                profile.refresh_from_db()
                analysis.creditos_cobrados = creditos_a_cobrar
                analysis.input_tokens = message.usage.input_tokens
                analysis.output_tokens = message.usage.output_tokens
                analysis.cache_creation_input_tokens = getattr(message.usage, 'cache_creation_input_tokens', None)
                analysis.cache_read_input_tokens = getattr(message.usage, 'cache_read_input_tokens', None)
            else:
                final_data = {
                    "error": "Claude no devolvió un bloque tool_use.",
                    "raw_music_data": analysis_data
                }
        else:
            final_data = {
                "error": "Falta la variable de entorno ANTHROPIC_TEST_API_KEY.",
                "raw_music_data": analysis_data
            }

        analysis.analysis_data = final_data
        analysis.save()

        yield json.dumps({
            'status': 'success',
            'data': final_data,
            'analysis_id': analysis.id,
            'creditos_analisis': profile.creditos_analisis,
            'creditos_bonus': profile.creditos_bonus,
        }) + "\n"

    except Exception as e:
        yield json.dumps({'status': 'error', 'message': str(e)}) + "\n"


@login_required
def orquestador_analizar(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST' and request.FILES.get('score_file'):
        if profile.creditos_analisis + profile.creditos_bonus <= 0:
            return JsonResponse({'status': 'error', 'message': 'No tenés créditos de análisis disponibles. Contactá al administrador.'}, status=403)

        score_file = request.FILES['score_file']
        name = request.POST.get('name', score_file.name)

        from .models import ScoreAnalysis
        version_de = None
        version_de_id = request.POST.get('version_de_id')
        if version_de_id:
            version_de = ScoreAnalysis.objects.filter(id=version_de_id, user=request.user).first()

        analysis = ScoreAnalysis.objects.create(
            user=request.user,
            name=name,
            score_file=score_file,
            version_de=version_de,
        )

        return StreamingHttpResponse(
            _generar_analisis_orquestacion(analysis, version_de, creditos_a_cobrar=1, omitir_chequeo_tamano=False),
            content_type='application/x-ndjson',
        )

    from .models import ScoreAnalysis
    analisis_previos = ScoreAnalysis.objects.filter(user=request.user).order_by('-created_at')[:20]

    return render(request, 'trainer/orquestador_analizar.html', {
        'creditos_analisis': profile.creditos_analisis,
        'creditos_bonus': profile.creditos_bonus,
        'analisis_previos': analisis_previos,
    })


@login_required
def orquestador_analizar_confirmado(request, analysis_id):
    """
    Segundo paso del flujo de obras grandes: el usuario ya vio el aviso de
    orquestador_analizar (status='confirmar') y decidió continuar. No recibe ni
    confía en ningún dato del cliente sobre tamaño/costo — solo el analysis_id, y
    todo lo demás (puntaje, créditos a cobrar) se re-deriva acá adentro.

    El .update() condicional "reclama" la fila antes de arrancar: si dos pestañas
    confirman el mismo analysis_id casi al mismo tiempo, solo una va a encontrar
    analysis_data todavía en NULL y afectar la fila — la otra recibe 0 filas
    actualizadas y devuelve 409 sin llegar a llamar a Claude ni cobrar nada.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido.'}, status=405)

    from .models import ScoreAnalysis
    reclamado = ScoreAnalysis.objects.filter(
        id=analysis_id, user=request.user, analysis_data__isnull=True
    ).update(analysis_data={})
    if not reclamado:
        return JsonResponse({'status': 'error', 'message': 'Este análisis ya fue confirmado o no existe.'}, status=409)

    analysis = get_object_or_404(ScoreAnalysis, id=analysis_id, user=request.user)

    return StreamingHttpResponse(
        _generar_analisis_orquestacion(analysis, analysis.version_de, creditos_a_cobrar=2, omitir_chequeo_tamano=True),
        content_type='application/x-ndjson',
    )


@login_required
def orquestador_historial(request):
    from .models import ScoreAnalysis
    analyses = ScoreAnalysis.objects.filter(user=request.user).order_by('-created_at')
    paginator = Paginator(analyses, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'trainer/orquestador_historial.html', {'page_obj': page_obj})


@login_required
def orquestador_fragmento_edicion(request, analysis_id):
    from .models import ScoreAnalysis
    analysis = get_object_or_404(ScoreAnalysis, id=analysis_id, user=request.user)

    parte = request.GET.get('parte', '')
    accion_tipo = request.GET.get('accion_tipo')
    direccion = request.GET.get('direccion')

    try:
        compas_desde = int(request.GET.get('compas_desde'))
        compas_hasta = int(request.GET.get('compas_hasta'))
    except (TypeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'compas_desde y compas_hasta deben ser enteros.'}, status=400)

    if accion_tipo != 'transponer_octava':
        return JsonResponse({'status': 'error', 'message': f"accion_tipo inválido: '{accion_tipo}'. Solo 'transponer_octava' genera pentagrama comparado."}, status=400)

    if direccion not in ('arriba', 'abajo'):
        return JsonResponse({'status': 'error', 'message': f"direccion inválida para transponer_octava: '{direccion}'. Debe ser 'arriba' o 'abajo'."}, status=400)

    try:
        score = _parsear_score_descifrado(analysis.score_file)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'No se pudo volver a leer el archivo original: {e}'}, status=500)

    tempos = score.recurse().getElementsByClass(music21.tempo.MetronomeMark)
    tempo_bpm = (tempos[0].number if tempos else None) or 100

    part = next((p for p in score.parts if p.partName == parte), None)
    if part is None:
        return JsonResponse({'status': 'error', 'message': f"La parte '{parte}' no existe en esta partitura."}, status=404)

    numeros_compas = [m.number for m in part.getElementsByClass(music21.stream.Measure)]
    if not numeros_compas:
        return JsonResponse({'status': 'error', 'message': f"La parte '{parte}' no tiene compases."}, status=404)

    min_compas, max_compas = min(numeros_compas), max(numeros_compas)
    if compas_desde > compas_hasta or compas_desde < min_compas or compas_hasta > max_compas:
        return JsonResponse({
            'status': 'error',
            'message': f"Rango de compases {compas_desde}-{compas_hasta} inválido. Esta parte va de {min_compas} a {max_compas}."
        }, status=400)

    try:
        original_xml, editado_xml, rango_mostrado = generar_fragmento_comparado(
            part, compas_desde, compas_hasta, accion_tipo, direccion, min_compas, max_compas
        )
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'No se pudo generar el fragmento: {e}'}, status=500)

    return JsonResponse({
        'status': 'success',
        'original_musicxml': original_xml,
        'editado_musicxml': editado_xml,
        'tempo_bpm': tempo_bpm,
        'rango_mostrado_desde': rango_mostrado[0],
        'rango_mostrado_hasta': rango_mostrado[1],
    })


def _calcular_mapa_registros(estadisticas_por_instrumento):
    """Versión en Python (sin JS) del mapa de registros, para renderizarlo en el PDF."""
    entradas = {
        nombre: v for nombre, v in (estadisticas_por_instrumento or {}).items()
        if v.get('ambito_min_ps') is not None
    }
    if not entradas:
        return []

    global_min = min(v['ambito_min_ps'] for v in entradas.values())
    global_max = max(v['ambito_max_ps'] for v in entradas.values())
    rango = (global_max - global_min) or 1

    mapa = []
    for nombre, v in entradas.items():
        left = round(((v['ambito_min_ps'] - global_min) / rango) * 100, 2)
        width = round(max(((v['ambito_max_ps'] - v['ambito_min_ps']) / rango) * 100, 1.5), 2)
        width = min(width, 100 - left)
        resto = round(100 - left - width, 2)
        mapa.append({'nombre': nombre, 'ambito': v['ambito'], 'left': left, 'width': width, 'resto': resto})
    return mapa


def _preparar_densidad_pdf(densidad_por_compas):
    """Precalcula la opacidad (0.0-1.0) de cada compás para el mapa de densidad del PDF."""
    resultado = []
    for item in (densidad_por_compas or []):
        total = item.get('total_instrumentos') or 0
        opacidad = round(item['instrumentos_activos'] / total, 2) if total else 0
        resultado.append({'compas': item['compas'], 'opacidad': opacidad})
    return resultado


@login_required
def orquestador_exportar_pdf(request, analysis_id):
    from .models import ScoreAnalysis
    analysis = get_object_or_404(ScoreAnalysis, id=analysis_id, user=request.user)
    data = analysis.analysis_data or {}

    html_string = render_to_string('trainer/orquestador_pdf.html', {
        'analysis': analysis,
        'data': data,
        'mapa_registros': _calcular_mapa_registros(data.get('estadisticas_por_instrumento')),
        'densidad_pdf': _preparar_densidad_pdf(data.get('densidad_por_compas')),
    })

    buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=buffer)
    if pisa_status.err:
        return JsonResponse({'status': 'error', 'message': 'No se pudo generar el PDF.'}, status=500)

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    nombre_archivo = "".join(c for c in analysis.name if c.isalnum() or c in " ._-").strip() or "analisis"
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}.pdf"'
    return response


@login_required
def orquestador_historial_detalle(request, analysis_id):
    from .models import ScoreAnalysis
    analysis = get_object_or_404(ScoreAnalysis, id=analysis_id, user=request.user)
    return render(request, 'trainer/orquestador_historial_detalle.html', {'analysis': analysis})


@login_required
def orquestador_generar_link(request, analysis_id):
    from .models import ScoreAnalysis
    analysis = get_object_or_404(ScoreAnalysis, id=analysis_id, user=request.user)

    if not analysis.share_token:
        analysis.share_token = secrets.token_urlsafe(32)
        analysis.save(update_fields=['share_token'])

    url_publica = request.build_absolute_uri(
        reverse('orquestador_publico', kwargs={'token': analysis.share_token})
    )
    return JsonResponse({'status': 'success', 'url': url_publica})


@login_required
def orquestador_revocar_link(request, analysis_id):
    from .models import ScoreAnalysis
    analysis = get_object_or_404(ScoreAnalysis, id=analysis_id, user=request.user)
    analysis.share_token = None
    analysis.save(update_fields=['share_token'])
    return JsonResponse({'status': 'success'})


def orquestador_publico(request, token):
    from .models import ScoreAnalysis
    analysis = get_object_or_404(ScoreAnalysis, share_token=token)
    return render(request, 'trainer/orquestador_publico.html', {'analysis': analysis})


def _notas_piano_para_ejercicio(score):
    """
    Extrae, de todas las partes del score, cada altura sonando: pitch, duración,
    compás y offset global en beats (necesario para alimentar AudioEngine.playSequence
    en el frontend). Recorre TODAS las partes, no solo la primera — un piano a dos
    manos se parsea en music21 como dos PartStaff independientes (uno por mano), no
    como un único Part; quedarse con parts[0] pierde la mano izquierda entera.

    Simplificaciones deliberadas para v1: las notas ligadas entre compases quedan
    como chips separados por segmento en vez de fusionarse (fusionar cadenas de
    ligaduras suma bastante lógica para un beneficio mayormente cosmético — el
    ejercicio es sobre elegir qué instrumento toca cada altura, no sobre articulación
    fina).

    Las grace notes (duration.isGrace, no solo quarterLength==0 — es el atributo
    explícito de music21 para esto) no se asignan individualmente: ningún orquestador
    separa un adorno de la nota que decora. En vez de descartarlas, se guardan en un
    buffer por compás y se adjuntan como campo 'graces' a TODAS las alturas del
    siguiente grupo de notas reales (si ese grupo es un acorde, así el ornamento viaja
    sin importar cuál de sus alturas termine asignada). El buffer se reinicia por
    compás — graces colgando al final de un compás sin nota real que las siga se
    descartan (caso borde raro).
    """
    notas = []
    contador = 0
    for part in score.parts:
        for m in part.getElementsByClass(music21.stream.Measure):
            graces_pendientes = []
            for el in m.recurse().notes:
                if isinstance(el, music21.chord.Chord):
                    pitches = el.pitches
                elif isinstance(el, music21.note.Note):
                    pitches = [el.pitch]
                else:
                    continue

                if el.duration.isGrace:
                    # duration.type preserva la figura notada (ej. 'eighth') incluso
                    # siendo grace (quarterLength siempre 0) — hace falta para que la
                    # grace generada no caiga al default de music21 ('quarter') al
                    # reconstruirla, que renderizaba distinto al original.
                    for p in pitches:
                        graces_pendientes.append({'pitch': p.nameWithOctave, 'ps': p.ps, 'tipo': el.duration.type})
                    continue

                duracion = el.duration.quarterLength
                if duracion == 0:
                    continue

                offset_global = el.getOffsetInHierarchy(part)
                graces_para_este_grupo = graces_pendientes
                graces_pendientes = []
                for p in pitches:
                    contador += 1
                    notas.append({
                        'id': f'n{contador}',
                        'compas': m.number,
                        'offset': offset_global,
                        'duracion_ql': duracion,
                        'pitch': p.nameWithOctave,  # nombre en inglés — lo espera AudioEngine.noteToMidiStr
                        'pitch_solfeo': _a_solfeo(p.nameWithOctave),  # solo para mostrar en el chip
                        'ps': p.ps,
                        'graces': graces_para_este_grupo,
                    })

    notas.sort(key=lambda n: (n['offset'], -n['ps']))
    return notas


@login_required
def orquestacion_ejercicio_lista(request):
    from .models import FragmentoOrquestacion
    fragmentos = FragmentoOrquestacion.objects.filter(activo=True).order_by('-created_at')
    return render(request, 'trainer/orquestacion_ejercicio_lista.html', {'fragmentos': fragmentos})


# Mismos tonos que los tokens --primary/--slate/--ochre/--green/--brown de
# static/css/gabinete.css (rediseño "Gabinete de estudio") -- estos 5 colores se usan
# como literales de pincel, no vía CSS, no hay forma de que Python lea las custom
# properties, así que quedan sincronizados a mano igual que COLOR_NOTA_EDITADA más
# arriba. Tienen que seguir siendo distinguibles entre sí Y del rojo de "nota editada"
# del analizador (#c9433f).
ZONAS_EJERCICIO_ORQUESTACION = [
    {'nombre': 'Violín I', 'color': '#6b3550', 'rango_key': 'Violín'},
    {'nombre': 'Violín II', 'color': '#3f5a6b', 'rango_key': 'Violín'},
    {'nombre': 'Viola', 'color': '#b5822e', 'rango_key': 'Viola'},
    {'nombre': 'Violonchelo', 'color': '#4b6b52', 'rango_key': 'Violonchelo'},
    {'nombre': 'Contrabajo', 'color': '#8c5a3f', 'rango_key': 'Contrabajo'},
]


@login_required
def orquestacion_ejercicio(request, fragmento_id):
    from .models import FragmentoOrquestacion
    fragmento = get_object_or_404(FragmentoOrquestacion, id=fragmento_id, activo=True)
    return render(request, 'trainer/orquestacion_ejercicio.html', {
        'fragmento': fragmento,
        'rangos_comodos_data': _serializar_rangos_comodos(),
        'zonas_data': ZONAS_EJERCICIO_ORQUESTACION,
    })


@login_required
def orquestacion_ejercicio_archivo(request, fragmento_id):
    """
    Sirve el contenido crudo del archivo (.musicxml/.xml/.mxl) para que OSMD lo
    cargue en el navegador. Deliberadamente NO se resuelve vía MEDIA_URL directo:
    en PythonAnywhere los archivos de media se sirven por fuera de Django, sin pasar
    por @login_required — serviría este fragmento "curado" a cualquiera con la URL,
    inconsistente con que el resto del ejercicio exige sesión. Tampoco se convierte
    a texto acá — se le pasan los bytes tal cual a OSMD, que ya sabe distinguir XML
    plano de .mxl comprimido, en vez de duplicar esa lógica en el servidor.
    """
    from .models import FragmentoOrquestacion
    fragmento = get_object_or_404(FragmentoOrquestacion, id=fragmento_id, activo=True)

    extension = pathlib.Path(fragmento.archivo.name).suffix.lower()
    content_type = 'application/vnd.recordare.musicxml' if extension == '.mxl' else 'application/vnd.recordare.musicxml+xml'

    return FileResponse(
        fragmento.archivo.open('rb'),
        content_type=content_type,
        filename=fragmento.archivo.name,
    )


@login_required
def cursos_list(request):
    from .models import Curso
    # No filtra por idioma: el contenido bilingüe vive DENTRO de cada
    # BloqueContenido (ver texto_markdown_en/etc. en models.py), no en filas de
    # Curso separadas por idioma -- un curso activo se lista para cualquier
    # idioma de sesión, cada bloque muestra su propio texto ES/EN al abrirlo.
    cursos = Curso.objects.filter(activo=True)
    return render(request, 'trainer/cursos_list.html', {'cursos': cursos})


@login_required
def curso_detail(request, curso_id):
    from .models import Curso
    curso = get_object_or_404(Curso, id=curso_id, activo=True)
    grados = curso.grados.filter(activo=True)
    return render(request, 'trainer/curso_detail.html', {'curso': curso, 'grados': grados})


@login_required
def tema_detail(request, curso_id, grado_numero, tema_slug):
    from .models import Curso, Grado, Tema
    from .services import render_markdown_seguro

    curso = get_object_or_404(Curso, id=curso_id, activo=True)
    grado = get_object_or_404(Grado, curso=curso, numero=grado_numero, activo=True)
    tema = get_object_or_404(Tema, grado=grado, slug=tema_slug, activo=True)

    # Navegación anterior/siguiente a través de TODO el curso (no solo dentro
    # del grado actual) -- orden estable: grado.numero primero, orden dentro
    # del grado después. select_related('grado') porque el link de cada uno
    # necesita el numero del grado al que pertenece, no necesariamente el
    # grado actual.
    temas_curso = list(
        Tema.objects.filter(grado__curso=curso, activo=True, grado__activo=True)
        .select_related('grado')
        .order_by('grado__numero', 'orden')
    )
    indice_actual = next(i for i, t in enumerate(temas_curso) if t.id == tema.id)
    tema_anterior = temas_curso[indice_actual - 1] if indice_actual > 0 else None
    tema_siguiente = temas_curso[indice_actual + 1] if indice_actual < len(temas_curso) - 1 else None

    bloques = list(tema.bloques.all())  # ya vienen en orden por Meta.ordering
    for bloque in bloques:
        if bloque.tipo == bloque.TEXTO:
            bloque.html_renderizado = render_markdown_seguro(bloque.texto_markdown_mostrado)
        elif bloque.tipo == bloque.EJEMPLO_PARTITURA:
            # es_mxl calculado server-side (mismo patrón que biblioteca_archivo/
            # orquestacion_ejercicio_archivo) -- nada de adivinar la extensión en JS.
            archivo_name = bloque.sheet_music.xml_file.name if bloque.sheet_music else bloque.fragmento_orquestacion.archivo.name
            bloque.es_mxl = pathlib.Path(archivo_name).suffix.lower() == '.mxl'

    response = render(request, 'trainer/tema_detail.html', {
        'curso': curso, 'grado': grado, 'tema': tema, 'bloques': bloques,
        'tema_anterior': tema_anterior, 'tema_siguiente': tema_siguiente,
    })
    # Django manda Referrer-Policy: same-origin por default (SecurityMiddleware,
    # ver SECURE_REFERRER_POLICY) -- eso hace que el navegador NO envíe referer
    # al cargar el iframe de YouTube/Vimeo de un bloque VIDEO, y YouTube devuelve
    # error 153 ("Error de configuración del reproductor") en videos con
    # reproducción restringida por dominio, que necesitan ver ese header para
    # verificar el origen. Confirmado en la práctica (no solo en la doc de
    # Google) con un video real. Scopeado a esta vista únicamente -- el resto
    # del sitio mantiene el default más estricto de Django; setdefault() en
    # SecurityMiddleware respeta un header ya seteado en la response.
    response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


@login_required
def bloque_imagen_archivo(request, bloque_id):
    """
    Sirve el archivo de un bloque IMAGEN -- mismo motivo que biblioteca_archivo/
    orquestacion_ejercicio_archivo: no resolver vía MEDIA_URL directo, que en
    PythonAnywhere se sirve por fuera de Django sin pasar por @login_required.
    """
    from .models import BloqueContenido
    bloque = get_object_or_404(BloqueContenido, id=bloque_id, tipo=BloqueContenido.IMAGEN)

    extension = pathlib.Path(bloque.imagen.name).suffix.lower()
    content_type = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.svg': 'image/svg+xml',
    }.get(extension, 'application/octet-stream')

    return FileResponse(
        bloque.imagen.open('rb'),
        content_type=content_type,
        filename=bloque.imagen.name,
    )


@login_required
def bloque_video_archivo(request, bloque_id):
    """
    Sirve el archivo de un bloque VIDEO con fuente Archivo subido -- mismo
    motivo que bloque_imagen_archivo (no exponer vía MEDIA_URL directo).
    FileResponse soporta el header Range de forma nativa (Django >= 3.0), así
    que arrastrar la barra de progreso del video no requiere nada especial acá.
    """
    from .models import BloqueContenido
    bloque = get_object_or_404(
        BloqueContenido, id=bloque_id, tipo=BloqueContenido.VIDEO, video_fuente=BloqueContenido.FUENTE_VIDEO_ARCHIVO,
    )

    extension = pathlib.Path(bloque.video_archivo.name).suffix.lower()
    content_type = {
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
        '.ogv': 'video/ogg',
        '.ogg': 'video/ogg',
        '.mov': 'video/quicktime',
    }.get(extension, 'application/octet-stream')

    return FileResponse(
        bloque.video_archivo.open('rb'),
        content_type=content_type,
        filename=bloque.video_archivo.name,
    )


@login_required
def orquestacion_ejercicio_datos(request, fragmento_id):
    from .models import FragmentoOrquestacion
    fragmento = get_object_or_404(FragmentoOrquestacion, id=fragmento_id, activo=True)

    try:
        score = music21.converter.parse(fragmento.archivo.path)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'No se pudo leer el archivo: {e}'}, status=500)

    tempos = score.recurse().getElementsByClass(music21.tempo.MetronomeMark)
    tempo_bpm = (tempos[0].number if tempos else None) or 100

    return JsonResponse({
        'status': 'success',
        'fragmento': {'nombre': fragmento.nombre, 'tempo_bpm': tempo_bpm},
        'notas': _notas_piano_para_ejercicio(score),
    })


ORDEN_PARTES_ORQUESTACION = ['Violín I', 'Violín II', 'Viola', 'Violonchelo', 'Contrabajo']

CLEFES_EJERCICIO_ORQUESTACION = {
    'Violín I': music21.clef.TrebleClef,
    'Violín II': music21.clef.TrebleClef,
    'Viola': music21.clef.AltoClef,
    'Violonchelo': music21.clef.BassClef,
    'Contrabajo': music21.clef.BassClef,
}

INSTRUMENTOS_EJERCICIO_ORQUESTACION = {
    'Violín I': music21.instrument.Violin,
    'Violín II': music21.instrument.Violin,
    'Viola': music21.instrument.Viola,
    'Violonchelo': music21.instrument.Violoncello,
    'Contrabajo': music21.instrument.Contrabass,
}

OCTAVAS_VALIDAS_EJERCICIO = (-1, 0, 1)

INTERVALO_POR_OCTAVA_EJERCICIO = {
    -1: music21.interval.Interval('P-8'),
    0: None,
    1: music21.interval.Interval('P8'),
}


def _transportar_pitch_preservando_grafia(nombre_pitch, octava):
    """
    Construye el Pitch SIEMPRE a partir del nombre original (nameWithOctave de
    music21, ej. "D#4"), nunca desde .ps — construir con Pitch(ps=...) le deja a
    music21 elegir la grafía por convención propia (ps=75 sale "E-5", no "D#5"),
    ensuciando la ortografía del original y generando becuadros espurios en notas
    vecinas (verificado). Transponer por un intervalo de OCTAVA JUSTA (P8/P-8, no
    aritmética de semitonos) preserva el nombre de nota y la alteración por
    definición — P8 de D#4 es D#5, nunca Eb5.
    """
    p = music21.pitch.Pitch(nombre_pitch)
    intervalo = INTERVALO_POR_OCTAVA_EJERCICIO.get(octava)
    if intervalo is not None:
        p = p.transpose(intervalo)
    return p


def _tiempo_y_armadura_por_compas(partes_originales, compases_totales):
    """
    Recorre todas las partes originales (no solo la primera) buscando dónde se declaran
    explícitamente compás/armadura, y rellena hacia adelante para los compases donde no
    se vuelven a declarar — soporta cambios de compás a mitad de la obra en vez de asumir
    un único compás constante.
    """
    tiempo_explicito = {}
    armadura = None
    for p in partes_originales:
        for m in p.getElementsByClass(music21.stream.Measure):
            if m.number not in tiempo_explicito:
                ts_list = m.getElementsByClass(music21.meter.TimeSignature)
                if ts_list:
                    tiempo_explicito[m.number] = ts_list[0]
            if armadura is None:
                ks_list = m.getElementsByClass(music21.key.KeySignature)
                if ks_list:
                    armadura = ks_list[0]

    resultado = {}
    actual = tiempo_explicito.get(1) or music21.meter.TimeSignature('4/4')
    for numero in range(1, compases_totales + 1):
        if numero in tiempo_explicito:
            actual = tiempo_explicito[numero]
        resultado[numero] = actual
    return resultado, armadura


def _offsets_inicio_compas(part):
    return {m.number: m.offset for m in part.getElementsByClass(music21.stream.Measure)}


def _armar_parte_orquestal(nombre, eventos_por_compas, compases_totales, tiempo_por_compas, armadura):
    """
    eventos_por_compas: {numero_compas: [{'offset_local', 'duracion', 'pitch' (music21.pitch.Pitch, ya con la octava aplicada y grafía heredada), 'graces'}, ...]}.
    Los compases sin eventos quedan enteramente en silencio. Los silencios se calculan
    a mano (antes de la primera nota, entre notas, después de la última) — makeRests()
    de music21 no dio resultados confiables en las pruebas (compases con duración
    incorrecta o directamente vacíos sin silencio explícito). Varias notas del mismo
    instrumento en el mismo offset (ej. dobles cuerdas) se combinan en un acorde en vez
    de insertarse superpuestas — los acordes no llevan graces en v1 (simplificación
    deliberada: decorar un acorde completo es mucho menos común y complica bastante el
    agrupamiento). Para una nota individual, sus graces (si tiene) se insertan como
    Note(...).getGrace() en el mismo offset, justo antes de la nota principal.
    """
    parte = music21.stream.Part()
    instr = INSTRUMENTOS_EJERCICIO_ORQUESTACION[nombre]()
    instr.partName = nombre
    parte.insert(0, instr)

    for numero in range(1, compases_totales + 1):
        compas = music21.stream.Measure(number=numero)
        tiempo_compas = tiempo_por_compas[numero]
        duracion_compas = tiempo_compas.barDuration.quarterLength

        if numero == 1:
            # Sin esto music21 no emite NINGÚN <clef> en el XML exportado (verificado) —
            # queda a criterio del renderizador, que es peor que elegir mal.
            compas.insert(0, CLEFES_EJERCICIO_ORQUESTACION[nombre]())
            if armadura is not None:
                compas.insert(0, copy.deepcopy(armadura))
            compas.insert(0, copy.deepcopy(tiempo_compas))
        elif tiempo_compas.ratioString != tiempo_por_compas[numero - 1].ratioString:
            compas.insert(0, copy.deepcopy(tiempo_compas))

        agrupados = {}
        for ev in eventos_por_compas.get(numero, []):
            clave = round(ev['offset_local'], 6)
            agrupados.setdefault(clave, []).append(ev)

        cursor = 0.0
        for offset_local in sorted(agrupados.keys()):
            grupo = agrupados[offset_local]
            offset_ajustado = min(offset_local, duracion_compas)
            if offset_ajustado > cursor:
                compas.insert(cursor, music21.note.Rest(quarterLength=offset_ajustado - cursor))
            duracion = min(max(e['duracion'] for e in grupo), duracion_compas - offset_ajustado)
            if duracion <= 0:
                continue
            if len(grupo) == 1:
                graces_del_grupo = grupo[0].get('graces', [])
                notas_grace = []
                for grace_info in graces_del_grupo:
                    # type explícito preserva la figura notada del adorno original (ej.
                    # 'eighth') — sin esto, la nota base cae al default de music21
                    # ('quarter') antes de volverse grace, y sale como negra en vez de
                    # corchea en el XML (verificado, causa probable de que el mordente
                    # se dibujara incompleto/distinto en OSMD).
                    grace_note = music21.note.Note(grace_info['pitch'], type=grace_info['tipo']).getGrace()
                    # Plica siempre hacia arriba en las grace notes — sin esto, el
                    # cálculo automático de plica por altura (convención normal para
                    # notas regulares) puede darles plica abajo si están agudas,
                    # dejando el mordente "dado vuelta". Solo afecta a las graces, no a
                    # la nota principal ni al resto de la parte.
                    grace_note.stemDirection = 'up'
                    notas_grace.append(grace_note)
                # Beam explícito entre las graces consecutivas, como en el original (dos
                # corcheas beameadas) — experimento acotado para ver si ayuda al
                # renderizado; no cambia el dato musical si no ayuda.
                if len(notas_grace) >= 2:
                    notas_grace[0].beams.append('start')
                    for intermedia in notas_grace[1:-1]:
                        intermedia.beams.append('continue')
                    notas_grace[-1].beams.append('stop')
                for grace_note in notas_grace:
                    compas.insert(offset_ajustado, grace_note)
                elemento = music21.note.Note(grupo[0]['pitch'], quarterLength=duracion)
            else:
                elemento = music21.chord.Chord(
                    [e['pitch'] for e in grupo], quarterLength=duracion
                )
            compas.insert(offset_ajustado, elemento)
            cursor = offset_ajustado + duracion

        if cursor < duracion_compas:
            compas.insert(cursor, music21.note.Rest(quarterLength=duracion_compas - cursor))

        parte.append(compas)

    # El exportador de music21 beamea automáticamente las corcheas sueltas cuando NO
    # hay ningún beam explícito en la parte — pero en cuanto agregamos el beam manual
    # de las graces, ese beaming automático dejó de correr para el resto del compás
    # (verificado: las corcheas principales quedaban sueltas). makeBeams() recalcula el
    # agrupamiento de corcheas según el compás, como un copista, y no pisa el beam
    # manual que ya tienen las graces (verificado con reparseo: ambos sobreviven juntos).
    parte.makeBeams(inPlace=True)
    parte.makeTies(inPlace=True)
    return parte


def _generar_score_orquestal(score_original, notas_por_id, asignaciones):
    """
    Arma un Score de music21 de 5 partes (Violín I, Violín II, Viola, Violonchelo,
    Contrabajo) a partir de las asignaciones del ejercicio de pintado. `asignaciones`
    es {notaId: [{'instrumento','octava'}, ...]} — una nota puede aparecer en varias
    partes a la vez (multi-asignación, ej. duplicar en octavas entre Violín I y II).
    Cada nota conserva compás/offset/duración originales; el modificador de octava de
    cada asignación se aplica ANTES de armar la nota, transponiendo por octava justa
    (P8/P-8) el Pitch original — nunca reconstruyendo desde .ps, que le deja a music21
    elegir la grafía por convención propia y puede cambiar D#4 por Eb4 (verificado,
    ver _transportar_pitch_preservando_grafia). El contrabajo, además, es instrumento
    transpositor real (Contrabass.transposition = P-8 en music21) — se arma el score
    con las alturas que SUENAN y se marca atSoundingPitch=True + toWrittenPitch() para
    que la parte de contrabajo salga escrita una octava arriba de lo que suena, como
    corresponde, preservando igual la grafía original (verificado); las demás partes
    no tienen transposition y quedan intactas.
    """
    partes_originales = list(score_original.parts)
    compases_totales = max(len(list(p.getElementsByClass(music21.stream.Measure))) for p in partes_originales)
    tiempo_por_compas, armadura = _tiempo_y_armadura_por_compas(partes_originales, compases_totales)
    offsets_inicio = _offsets_inicio_compas(partes_originales[0])

    eventos_por_instrumento = {nombre: {} for nombre in ORDEN_PARTES_ORQUESTACION}
    for nota_id, lista in asignaciones.items():
        evento = notas_por_id[nota_id]
        compas = evento['compas']
        offset_local = evento['offset'] - offsets_inicio.get(compas, 0.0)
        for asignacion in lista:
            instrumento = asignacion['instrumento']
            octava = asignacion['octava']
            graces_transportadas = [
                {'pitch': _transportar_pitch_preservando_grafia(g['pitch'], octava), 'tipo': g['tipo']}
                for g in evento.get('graces', [])
            ]
            eventos_por_instrumento[instrumento].setdefault(compas, []).append({
                'offset_local': offset_local,
                'duracion': evento['duracion_ql'],
                'pitch': _transportar_pitch_preservando_grafia(evento['pitch'], octava),
                'graces': graces_transportadas,
            })

    score = music21.stream.Score()
    for nombre in ORDEN_PARTES_ORQUESTACION:
        parte = _armar_parte_orquestal(
            nombre, eventos_por_instrumento[nombre], compases_totales, tiempo_por_compas, armadura
        )
        score.insert(0, parte)

    score.atSoundingPitch = True
    score_escrito = score.toWrittenPitch()

    exporter = music21.musicxml.m21ToXml.GeneralObjectExporter(score_escrito)
    return exporter.parse().decode('utf-8')


@login_required
def orquestacion_ejercicio_generar(request, fragmento_id):
    """
    Recibe las asignaciones (nota -> instrumento/octava) del ejercicio de pintado y
    devuelve el MusicXML de la partitura orquestal de 5 partes. No persiste nada en
    v1 — se genera y se devuelve en la respuesta como string, mismo patrón que
    original_musicxml/editado_musicxml en orquestador_fragmento_edicion (un futuro
    botón de descarga sería trivial: crear un Blob del string, sin tocar el backend).

    Nunca confía en pitch/offset/duración del cliente — solo en qué notaId citó y qué
    instrumento/octava eligió; todo lo demás se re-deriva de _notas_piano_para_ejercicio
    sobre el archivo real, igual que orquestacion_ejercicio_datos.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido.'}, status=405)

    from .models import FragmentoOrquestacion
    fragmento = get_object_or_404(FragmentoOrquestacion, id=fragmento_id, activo=True)

    try:
        body = json.loads(request.body)
        asignaciones_crudas = body.get('asignaciones')
        if not isinstance(asignaciones_crudas, dict):
            raise ValueError('falta asignaciones')
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'Body inválido: se esperaba JSON con "asignaciones".'}, status=400)

    nombres_validos = {z['nombre'] for z in ZONAS_EJERCICIO_ORQUESTACION}
    asignaciones = {}
    for nota_id, lista_cruda in asignaciones_crudas.items():
        if not isinstance(lista_cruda, list) or not lista_cruda:
            return JsonResponse({'status': 'error', 'message': f'Asignación inválida para "{nota_id}": se esperaba una lista no vacía.'}, status=400)

        lista_validada = []
        instrumentos_vistos = set()
        for valor in lista_cruda:
            if not isinstance(valor, dict):
                return JsonResponse({'status': 'error', 'message': f'Asignación inválida para "{nota_id}".'}, status=400)
            instrumento = valor.get('instrumento')
            octava = valor.get('octava')
            if instrumento not in nombres_validos:
                return JsonResponse({'status': 'error', 'message': f'Instrumento inválido: {instrumento!r}.'}, status=400)
            if octava not in OCTAVAS_VALIDAS_EJERCICIO:
                return JsonResponse({'status': 'error', 'message': f'Octava inválida: {octava!r}.'}, status=400)
            if instrumento in instrumentos_vistos:
                return JsonResponse({
                    'status': 'error',
                    'message': f'"{nota_id}" repite el instrumento {instrumento!r} más de una vez — dato ambiguo.',
                }, status=400)
            instrumentos_vistos.add(instrumento)
            lista_validada.append({'instrumento': instrumento, 'octava': octava})
        asignaciones[nota_id] = lista_validada

    try:
        score_original = music21.converter.parse(fragmento.archivo.path)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'No se pudo leer el archivo: {e}'}, status=500)

    notas_por_id = {n['id']: n for n in _notas_piano_para_ejercicio(score_original)}
    ids_desconocidos = [nid for nid in asignaciones if nid not in notas_por_id]
    if ids_desconocidos:
        return JsonResponse({
            'status': 'error',
            'message': f'{len(ids_desconocidos)} nota(s) no existen en este fragmento (ej. {ids_desconocidos[0]}).',
        }, status=400)

    if not asignaciones:
        return JsonResponse({'status': 'error', 'message': 'No hay ninguna nota asignada todavía.'}, status=400)

    try:
        musicxml = _generar_score_orquestal(score_original, notas_por_id, asignaciones)
    except Exception as e:
        logger.exception('orquestacion_ejercicio_generar: fallo armando la partitura orquestal')
        return JsonResponse({'status': 'error', 'message': f'No se pudo generar la partitura: {e}'}, status=500)

    return JsonResponse({'status': 'success', 'musicxml': musicxml})


@login_required
def biblioteca_list(request):
    from django.db.models import Q
    col_slug = request.GET.get('collection')
    favorites_only = request.GET.get('favorites') == 'true'
    query = request.GET.get('q', '').strip()

    scores = SheetMusic.objects.all().order_by('-created_at')

    if col_slug:
        scores = scores.filter(collections__slug=col_slug)

    if favorites_only:
        scores = scores.filter(favorited_by__user=request.user)

    if query:
        scores = scores.filter(Q(title__icontains=query) | Q(composer__icontains=query))

    collections = Collection.objects.all()
    user_favorites = Favorite.objects.filter(user=request.user).values_list('sheet_music_id', flat=True)
    user_progress = {
        p.sheet_music_id: p.completion_percentage
        for p in SheetMusicProgress.objects.filter(user=request.user)
    }
    # "Abiertas hace poco" (sidebar del rediseño "Gabinete de estudio") -- reusa
    # SheetMusicProgress.last_practiced, que ya se actualiza solo (auto_now) cada
    # vez que se guarda progreso, no hace falta trackear nada nuevo.
    recientes = SheetMusicProgress.objects.filter(user=request.user).select_related('sheet_music').order_by('-last_practiced')[:5]

    context = {
        'scores': scores,
        'collections': collections,
        'user_favorites': user_favorites,
        'user_progress': user_progress,
        'current_collection': col_slug,
        'favorites_only': favorites_only,
        'query': query,
        'recientes': recientes,
    }
    return render(request, 'trainer/biblioteca_list.html', context)

@login_required
def toggle_favorite(request, score_id):
    if request.method == 'POST':
        score = get_object_or_404(SheetMusic, id=score_id)
        fav, created = Favorite.objects.get_or_create(user=request.user, sheet_music=score)
        if not created:
            fav.delete()
            return JsonResponse({'status': 'removed'})
        return JsonResponse({'status': 'added'})
    return JsonResponse({'error': 'Invalid method'}, status=400)

@login_required
# @csrf_exempt deliberado, NO un descuido: se llama exclusivamente vía
# navigator.sendBeacon() en el handler de beforeunload de biblioteca_play.html, que por
# diseño del navegador no permite mandar headers custom (no hay forma de adjuntar
# X-CSRFToken a un sendBeacon). Impacto de un CSRF forjado acá es bajo -- la vista ya
# filtra user=request.user, así que lo peor que logra un atacante es loguear una
# sesión de estudio falsa a nombre de la víctima; no hay alcance a datos de otro
# usuario ni gasto de crédito. Decisión tomada en la auditoría de seguridad de la
# sesión, no reabrir sin revisar de nuevo el resto de las vistas @csrf_exempt del
# archivo (todas las demás SÍ perdieron el exempt).
@csrf_exempt
def log_study_session(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            score_id = data.get('score_id')
            duration_sec = data.get('duration_seconds', 0)
            bpm = data.get('bpm_used', 100)
            plays = data.get('play_count', 1)

            score = get_object_or_404(SheetMusic, id=score_id)
            
            # 1. Registrar sesión
            StudySession.objects.create(
                user=request.user,
                sheet_music=score,
                duration_seconds=duration_sec,
                bpm_used=bpm,
                play_count=plays
            )

            # 2. Actualizar Progreso Individual
            progress, _ = SheetMusicProgress.objects.get_or_create(user=request.user, sheet_music=score)
            progress.total_time_seconds += duration_sec
            progress.total_plays += plays
            
            # Simple cálculo: 10 plays = 100% o 10 minutos = 100%
            time_perc = min((progress.total_time_seconds / 600) * 100, 100)
            play_perc = min((progress.total_plays / 10) * 100, 100)
            progress.completion_percentage = int(max(time_perc, play_perc))
            progress.save()

            # 3. Actualizar Objetivos Diarios (Simple lógica global para TIME)
            today_goals = UserDailyGoal.objects.filter(user=request.user, date=timezone.now().date())
            for ug in today_goals:
                if ug.goal.goal_type == 'TIME' and not ug.is_completed:
                    ug.current_value += duration_sec // 60
                    if ug.current_value >= ug.goal.target_value:
                        ug.is_completed = True
                    ug.save()

            return JsonResponse({'status': 'ok'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid method'}, status=400)

@login_required
def biblioteca_play(request, score_id):
    sheet = get_object_or_404(SheetMusic, id=score_id)
    is_favorite = Favorite.objects.filter(user=request.user, sheet_music=sheet).exists()
    
    from .models import SheetMarker, SheetNote, Playlist, RehearsalConfig, MusicalProject, ProjectSection
    markers = SheetMarker.objects.filter(user=request.user, sheet_music=sheet)
    notes = SheetNote.objects.filter(user=request.user, sheet_music=sheet).order_by('-created_at')
    playlists = Playlist.objects.filter(user=request.user)
    rehearsal_configs = RehearsalConfig.objects.filter(user=request.user, sheet_music=sheet)
    
    project_id = request.GET.get('project_id')
    active_project = None
    project_sections = []
    if project_id:
        active_project = MusicalProject.objects.filter(id=project_id, user=request.user, sheet_music=sheet).first()
        if active_project:
            project_sections = ProjectSection.objects.filter(project=active_project)
    
    return render(request, 'trainer/biblioteca_play.html', {
        'sheet': sheet,
        'is_favorite': is_favorite,
        'markers': markers,
        'notes': notes,
        'playlists': playlists,
        'rehearsal_configs': rehearsal_configs,
        'active_project': active_project,
        'project_sections': project_sections,
    })


@login_required
def biblioteca_archivo(request, score_id):
    """
    Sirve el contenido crudo del xml_file -- mismo motivo que
    orquestacion_ejercicio_archivo: en PythonAnywhere /media/ se sirve por fuera de
    Django (Apache/nginx), sin @login_required, así que resolver esto vía MEDIA_URL
    directo (como hacía antes biblioteca_play.html) dejaba cualquier partitura de la
    Biblioteca descargable sin sesión -- confirmado en la práctica, no solo en teoría.
    Sin filtro por user=: SheetMusic es un recurso compartido de la Biblioteca (no
    privado de un usuario), @login_required es exactamente el mismo nivel de
    protección que ya tiene la propia página de biblioteca_play.
    """
    sheet = get_object_or_404(SheetMusic, id=score_id)

    extension = pathlib.Path(sheet.xml_file.name).suffix.lower()
    content_type = 'application/vnd.recordare.musicxml' if extension == '.mxl' else 'application/vnd.recordare.musicxml+xml'

    return FileResponse(
        sheet.xml_file.open('rb'),
        content_type=content_type,
        filename=sheet.xml_file.name,
    )


def _secuencia_compases_canonica(score):
    """
    Determina el orden de EJECUCIÓN de números de compás (repeticiones/voltas ya
    resueltas), de forma robusta ante un caso real confirmado con music21: la barra de
    repetición no siempre queda marcada en TODAS las partes de una partitura de piano a
    dos manos -- algunos exportadores de MusicXML solo la escriben en una de las dos
    (aunque visualmente la barra atraviesa las dos pentagramas del sistema). Si se llama
    a expandRepeats() sobre cada parte por separado con SUS propias barras, la parte sin
    la marca queda sin expandir mientras la otra sí -- y las dos manos quedan
    completamente desalineadas al reconstruir (síntoma real reportado: "primero suena
    la mano derecha, después la izquierda", a los saltos).

    Se expande cada parte por separado y se usa como canónica la secuencia de números de
    compás de la que ganó MÁS compases al expandir -- esa es la que capturó la
    repetición real. Con partituras bien formadas (marcada igual en todas las partes, o
    sin repeticiones en absoluto) todas las secuencias coinciden, da lo mismo cuál se
    elija.
    """
    secuencias = []
    for part in score.parts:
        parcial = music21.stream.Score()
        parcial.insert(0, part)
        try:
            expandido = parcial.expandRepeats()
            medidas = list(expandido.parts[0].getElementsByClass(music21.stream.Measure))
            secuencias.append([m.number for m in medidas])
        except Exception:
            secuencias.append([m.number for m in part.getElementsByClass(music21.stream.Measure)])

    if not secuencias:
        return []
    return max(secuencias, key=len)


def _eventos_ejecucion(score):
    """
    Arma la secuencia de EJECUCIÓN (orden real en que suena la pieza, con
    repeticiones/voltas expandidas) a partir del score ORIGINAL sin expandir, guiándose
    por _secuencia_compases_canonica() -- no por Score.expandRepeats() directo, que
    puede desalinear partes (ver esa función). Para cada compás de la secuencia
    canónica, se busca ese número de compás en CADA parte del score original (por
    número, no por posición) y se recorren juntas -- si una parte no tiene una segunda
    ocurrencia física de un compás repetido (porque nunca tuvo la barra marcada), se
    reusa su única copia, que es exactamente lo correcto: esa mano suena igual las dos
    veces.

    Un registro por altura real que suena, agrupadas por paso_ejecucion (una parada
    rítmica -- varias alturas comparten paso_ejecucion si suenan juntas, sea por acorde
    o por varias partes coincidiendo en el mismo tiempo). paso_en_compas identifica la
    parada DENTRO de su compás (se reinicia en cada ocurrencia, incluidas las
    repeticiones) -- es lo que el frontend usa para ubicar la parada exacta sin comparar
    offsets flotantes contra el recorrido del cursor de OSMD.

    Grace notes (duration.isGrace, no quarterLength==0 -- mismo criterio que
    _notas_piano_para_ejercicio): SÍ viajan en la secuencia acá (a diferencia de esa
    función, que las agrupa como adorno de un instrumento) porque tienen que sonar.
    Comparten paso_en_compas con la nota principal que las sigue -- no consumen una
    parada propia, porque el cursor de OSMD tampoco se mueve por ellas.

    Ligaduras de prolongación (note.tie, type start/continue/stop -- no chequeado antes,
    causaba que la nota se reatacara en cada segmento en vez de sonar prolongada): la
    nota que ABRE la ligadura se emite normal y queda "abierta"; cada segmento que la
    continúa NO se emite como ataque nuevo -- se le suma su duracion_ql a la nota que
    abrió la ligadura, y se marca es_ligadura_continuacion=True para que el frontend
    sepa que existe (la sigue usando para mover el cursor) pero no la dispare ni la
    ilumine, porque ya forma parte del sonido de la nota original. Clave por (parte,
    altura) -- dos manos pueden ligar la misma altura al mismo tiempo sin confundirse
    entre sí. Simplificación deliberada: se usa el tie del Chord/Note completo, no por
    altura individual dentro de un acorde (igual nivel de simplificación que el resto de
    esta función, que ya trata la duración de un acorde como una sola).
    """
    secuencia_canonica = _secuencia_compases_canonica(score)
    if not secuencia_canonica:
        return []

    partes = list(score.parts)
    medidas_por_parte = []
    for part in partes:
        mapa = {}
        for m in part.getElementsByClass(music21.stream.Measure):
            mapa.setdefault(m.number, []).append(m)
        medidas_por_parte.append(mapa)

    eventos = []
    paso_ejecucion = -1
    ocurrencias_usadas = [dict() for _ in partes]  # por parte: {numero_compas: cuántas veces ya se usó}
    ligadura_abierta = {}  # (idx_parte, ps) -> evento (dict, ya en `eventos`) que se sigue extendiendo

    # offset_global: posición absoluta en negras desde el INICIO de la ejecución (repeticiones
    # ya contadas como tiempo real transcurrido). Es el reemplazo del truco anterior del
    # frontend de acumular `time += max(duración de las notas del paso)` -- ese cálculo se
    # rompe apenas un paso tiene una nota de duración larga sonando en simultáneo con una más
    # corta que ataca antes que termine (larga tapa a la corta, o -- el caso real que lo
    # confirmó -- una ligadura: el segmento de continuación tiene su PROPIA duración local
    # larga aunque no representa un ataque nuevo, e igual competía por el "máximo" e inflaba
    # el avance del timeline). offset_global se ancla directo a la aritmética real de
    # duraciones de compás (barDuration, la duración nominal según el compás vigente, no el
    # contenido) -- exactamente el mismo principio que ya usa sin heurísticas el camino
    # IMPRESO (ts.RealValue de OSMD, un timestamp absoluto real).
    offset_global = 0.0

    for compas_num in secuencia_canonica:
        entradas = []
        duracion_compas = None
        for idx_parte, mapa in enumerate(medidas_por_parte):
            opciones = mapa.get(compas_num)
            if not opciones:
                continue
            ocurrencia = ocurrencias_usadas[idx_parte].get(compas_num, 0)
            m = opciones[ocurrencia] if ocurrencia < len(opciones) else opciones[-1]
            ocurrencias_usadas[idx_parte][compas_num] = ocurrencia + 1
            if duracion_compas is None:
                # barDuration = duración nominal del compás según el compás vigente (heredado
                # por contexto si este compás puntual no trae su propio <time>), no la suma
                # del contenido real -- correcto incluso si el compás está incompleto/raro.
                duracion_compas = float(m.barDuration.quarterLength)

            for el in m.recurse().notes:
                if isinstance(el, music21.harmony.Harmony):
                    # Cifrado (ChordSymbol, ej. "F"/"Am7" escrito arriba del pentagrama):
                    # music21.harmony.Harmony hereda de music21.chord.Chord (mismo pitches
                    # de las notas que forman el acorde), así que sin este chequeo el
                    # isinstance(el, Chord) de abajo lo confunde con un acorde real que
                    # SUENA -- entra a la secuencia con quarterLength 0 (duración por
                    # defecto de un cifrado) y duplicado (a menudo el cifrado se repite
                    # arriba de cada pentagrama del sistema). Síntoma real confirmado con
                    # un archivo de un usuario: notas fantasma de duración 0 intercaladas
                    # con las reales, deformando el audio y desalineando las manos.
                    continue
                if isinstance(el, music21.chord.Chord):
                    pitches = el.pitches
                elif isinstance(el, music21.note.Note):
                    pitches = [el.pitch]
                else:
                    continue
                offset_en_compas = el.getOffsetInHierarchy(m)
                tie_tipo = el.tie.type if el.tie is not None else None
                entradas.append((offset_en_compas, el.duration.isGrace, pitches, el.duration.quarterLength, idx_parte, tie_tipo))

        # Ordenado por offset -- se combinan elementos de partes DISTINTAS (measure
        # objects distintos), así que no hay garantía de orden entre ellas sin ordenar a mano.
        entradas.sort(key=lambda e: (e[0], e[1]))

        paso_en_compas = -1
        offset_actual = None
        graces_pendientes = []
        for offset_en_compas, es_grace, pitches, duracion, idx_parte, tie_tipo in entradas:
            # float() acá también -- offset_en_compas puede ser Fraction por el mismo motivo
            # que duracion (tresillos), y offset_global_evento viaja en el JSON.
            offset_global_evento = round(offset_global + float(offset_en_compas), 6)

            if es_grace:
                for p in pitches:
                    graces_pendientes.append({'pitch': p.nameWithOctave, 'ps': p.ps})
                continue

            if offset_en_compas != offset_actual:
                offset_actual = offset_en_compas
                paso_en_compas += 1
                paso_ejecucion += 1

            for g in graces_pendientes:
                eventos.append({
                    'paso_ejecucion': paso_ejecucion, 'compas_impreso': compas_num, 'paso_en_compas': paso_en_compas,
                    'offset_global': offset_global_evento,
                    'pitch': g['pitch'], 'ps': g['ps'], 'duracion_ql': 0.25, 'es_grace': True,
                    'es_ligadura_continuacion': False,
                })
            graces_pendientes = []

            alturas_de_este_elemento = set()
            for p in pitches:
                # Un mismo Chord de music21 puede traer la MISMA altura escrita dos veces
                # (doblado de octava/unísono dentro de un mismo acorde -- notación real de
                # piano; confirmado empíricamente que music21 NO deduplica esto solo:
                # Chord(['C4','E4','G4','C4']).pitches trae las dos C4). Sin este chequeo,
                # esa altura entraba dos veces con el mismo offset -- el frontend terminaba
                # disparando la misma nota dos veces en el mismo instante exacto, y Tone.js
                # tira "Start time must be strictly greater than previous start time" en el
                # segundo trigger (mata ESE trigger nada más, pero como consecuencia esa
                # nota ni suena ni se ilumina). Es la MISMA tecla física -- se descarta el
                # duplicado, se conserva el primero. Alcance deliberadamente angosto (solo
                # duplicados DENTRO del mismo elemento/acorde, no entre partes distintas):
                # un unísono legítimo entre dos manos son dos objetos Chord/Note distintos,
                # cada uno con su propia ligadura si la tuviera -- fusionarlos ahí requeriría
                # decidir de cuál de las dos conservar el estado de ligadura, sin evidencia
                # todavía de que haga falta. Ese caso, si existe, lo absorbe el frontend
                # (deduplica el disparo de audio por altura al agendar, sin tocar esta
                # secuencia) sin necesidad de resolverlo acá.
                if p.ps in alturas_de_este_elemento:
                    continue
                alturas_de_este_elemento.add(p.ps)

                clave = (idx_parte, p.ps)
                # duracion (el.duration.quarterLength) viene como fractions.Fraction para
                # cualquier valor no binario -- tresillos y demás tuplets, el caso real que
                # tumbaba este endpoint con un 500 (json.dumps no sabe serializar Fraction,
                # y el try/except de la vista solo cubre la construcción de `eventos`, no el
                # JsonResponse posterior). float() + round a 6 decimales acá, en el único
                # punto donde `duracion` entra a un evento, para que ningún campo del JSON
                # de salida pueda ser un Fraction -- 6 decimales alcanza para que un
                # Fraction(1,3) viaje estable como 0.333333 sin ruido de punto flotante en
                # la comparación de tolerancia (<0.01) que ya usa el frontend.
                duracion_json = round(float(duracion), 6)
                if tie_tipo in ('stop', 'continue') and clave in ligadura_abierta:
                    ligadura_abierta[clave]['duracion_ql'] = round(ligadura_abierta[clave]['duracion_ql'] + duracion_json, 6)
                    eventos.append({
                        'paso_ejecucion': paso_ejecucion, 'compas_impreso': compas_num, 'paso_en_compas': paso_en_compas,
                        'offset_global': offset_global_evento,
                        'pitch': p.nameWithOctave, 'ps': p.ps, 'duracion_ql': duracion_json, 'es_grace': False,
                        'es_ligadura_continuacion': True,
                    })
                    if tie_tipo == 'stop':
                        del ligadura_abierta[clave]
                else:
                    nuevo_evento = {
                        'paso_ejecucion': paso_ejecucion, 'compas_impreso': compas_num, 'paso_en_compas': paso_en_compas,
                        'offset_global': offset_global_evento,
                        'pitch': p.nameWithOctave, 'ps': p.ps, 'duracion_ql': duracion_json, 'es_grace': False,
                        'es_ligadura_continuacion': False,
                    }
                    eventos.append(nuevo_evento)
                    if tie_tipo == 'start':
                        ligadura_abierta[clave] = nuevo_evento

        if duracion_compas is not None:
            offset_global += duracion_compas
    return eventos


@login_required
def biblioteca_secuencia_ejecucion(request, score_id):
    """
    Secuencia de reproducción en orden de EJECUCIÓN (repeticiones/voltas expandidas,
    grace notes incluidas), para que biblioteca_play.html no dependa únicamente del
    orden IMPRESO que entrega el cursor de OSMD. Ver el plan de la sesión que lo
    introdujo para el detalle de la investigación con music21.
    """
    sheet = get_object_or_404(SheetMusic, id=score_id)
    try:
        score = music21.converter.parse(sheet.xml_file.path)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'No se pudo leer el archivo: {e}'})

    if score.recurse().getElementsByClass(music21.repeat.RepeatExpression):
        # D.C./D.S./Fine/Coda: verificado que expandRepeats() renumera los compases
        # repetidos en vez de conservar el número impreso (a diferencia de
        # bar.Repeat+RepeatBracket) -- rompe el ancla compas_impreso. No se intenta.
        return JsonResponse({'status': 'sin_soporte', 'motivo': 'repeticion_compleja'})

    try:
        eventos = _eventos_ejecucion(score)
    except Exception as e:
        return JsonResponse({'status': 'sin_soporte', 'motivo': f'expansion_fallo: {e}'})

    try:
        return JsonResponse({'status': 'success', 'eventos': eventos})
    except TypeError as e:
        # Red de seguridad: json.dumps corre DENTRO del constructor de JsonResponse, fuera
        # del try/except de arriba -- un campo no serializable (el caso real ya visto:
        # fractions.Fraction de un tresillo colándose en duracion_ql antes del fix de más
        # arriba) escapaba como un 500 crudo de Django en vez de cualquiera de los
        # 'sin_soporte' ya manejados. Si algún campo nuevo se cuela sin convertir en el
        # futuro, esto lo convierte en un fallback prolijo -- nunca una pieza que deja de
        # reproducirse por un error no capturado.
        return JsonResponse({'status': 'sin_soporte', 'motivo': f'serializacion_fallo: {e}'})


@login_required
def record_attempt(request, game_slug):
    if request.method == 'POST':
        try:
            game = Game.objects.get(slug=game_slug)
            data = json.loads(request.body)
            presented_question = data.get('presented_question')
            guessed_answer = data.get('guessed_answer')
            is_correct = data.get('is_correct')
            response_time_ms = data.get('response_time_ms')
            
            Attempt.objects.create(
                user=request.user,
                game=game,
                presented_question=presented_question,
                guessed_answer=guessed_answer,
                is_correct=is_correct,
                response_time_ms=response_time_ms
            )

            score, _ = Score.objects.get_or_create(user=request.user, game=game)
            score.total_answers += 1
            
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            today = timezone.localdate()
            if profile.last_active_date != today:
                if profile.last_active_date and (today - profile.last_active_date).days == 1:
                    profile.current_daily_streak += 1
                else:
                    profile.current_daily_streak = 1
                
                if profile.current_daily_streak > profile.max_daily_streak:
                    profile.max_daily_streak = profile.current_daily_streak
                profile.last_active_date = today

            xp_gain = 0
            if is_correct:
                score.correct_answers += 1
                score.current_streak += 1
                score.total_points += 10 + (score.current_streak * 2)
                if score.current_streak > score.max_streak:
                    score.max_streak = score.current_streak
                
                xp_gain = 10
                if score.game.slug == 'dictado-melodico':
                    path_slug = 'trainer_dictado_melodico'
                elif score.game.slug == 'lectura-musical':
                    path_slug = 'trainer_lectura_musical'
                else:
                    path_slug = 'trainer_' + score.game.slug.replace('-', '_')
                if score.current_streak == 10:
                    xp_gain += 50
                elif score.current_streak == 5:
                    xp_gain += 25
            else:
                score.current_streak = 0
                xp_gain = 2
            
            profile.total_xp += xp_gain
            profile.user_level = get_user_level_for_xp(profile.total_xp)
            
            lvl_info = score.level_info
            score.level = lvl_info['level']
            
            profile.save()
            score.save()
            check_achievements(request.user)
            
            stats_data = get_game_stats(request.user, game)

            return JsonResponse({
                'status': 'success',
                'current_streak': score.current_streak,
                'max_streak': score.max_streak,
                'total_points': score.total_points,
                'level': score.level,
                'accuracy': score.accuracy,
                'incorrect_answers': stats_data['incorrect_answers'],
                'avg_time': stats_data['avg_time'],
                'hardest': stats_data['hardest'],
                'xp_gained': xp_gain,
                'user_level': profile.user_level,
                'level_info': lvl_info
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)

@login_required
def add_sheet_marker(request, score_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            sheet = get_object_or_404(SheetMusic, id=score_id)
            measure = data.get('measure')
            text = data.get('text')
            
            from .models import SheetMarker
            SheetMarker.objects.create(
                user=request.user,
                sheet_music=sheet,
                measure=measure,
                text=text
            )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@login_required
def add_sheet_note(request, score_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            sheet = get_object_or_404(SheetMusic, id=score_id)
            text = data.get('text')
            
            from .models import SheetNote
            SheetNote.objects.create(
                user=request.user,
                sheet_music=sheet,
                text=text
            )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@login_required
def playlists_list(request):
    from .models import Playlist
    if request.method == 'POST':
        name = request.POST.get('name')
        if name:
            Playlist.objects.create(user=request.user, name=name)
        return redirect('playlists_list')
        
    playlists = Playlist.objects.filter(user=request.user)
    return render(request, 'trainer/playlists_list.html', {'playlists': playlists})

@login_required
def playlist_add_sheet(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            playlist_id = data.get('playlist_id')
            score_id = data.get('score_id')
            
            from .models import Playlist, PlaylistSheet
            playlist = get_object_or_404(Playlist, id=playlist_id, user=request.user)
            sheet = get_object_or_404(SheetMusic, id=score_id)
            
            last_order = playlist.items.count()
            PlaylistSheet.objects.create(playlist=playlist, sheet_music=sheet, order=last_order + 1)
            
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@login_required
def save_rehearsal_config(request, score_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            from .models import RehearsalConfig, SheetMusic
            sheet = get_object_or_404(SheetMusic, id=score_id)
            config = RehearsalConfig.objects.create(
                user=request.user,
                sheet_music=sheet,
                name=data.get('name', 'Mi Ensayo'),
                start_measure=int(data.get('start_measure', 1)),
                end_measure=int(data.get('end_measure', 1)),
                start_bpm=int(data.get('start_bpm', 60)),
                end_bpm=int(data.get('end_bpm', 100)),
                bpm_step=int(data.get('bpm_step', 5))
            )
            return JsonResponse({'status': 'success', 'config_id': config.id})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def log_rehearsal_session(request, score_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            from .models import RehearsalLog, RehearsalConfig, SheetMusic
            sheet = get_object_or_404(SheetMusic, id=score_id)
            config_id = data.get('config_id')
            config = RehearsalConfig.objects.filter(id=config_id).first() if config_id else None
            
            RehearsalLog.objects.create(
                user=request.user,
                sheet_music=sheet,
                rehearsal_config=config,
                repetitions_done=int(data.get('repetitions', 0)),
                time_spent_seconds=int(data.get('time_spent', 0)),
                max_bpm_reached=int(data.get('max_bpm', 0))
            )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def proyectos_list(request):
    from .models import MusicalProject
    proyectos = MusicalProject.objects.filter(user=request.user).order_by('-last_practice')
    return render(request, 'trainer/proyectos_list.html', {'proyectos': proyectos})

@login_required
def proyecto_detail(request, project_id):
    from .models import MusicalProject
    proyecto = get_object_or_404(MusicalProject, id=project_id, user=request.user)
    return render(request, 'trainer/proyecto_detail.html', {'proyecto': proyecto})

@login_required
def api_create_project(request, score_id):
    if request.method == 'POST':
        try:
            from .models import MusicalProject, SheetMusic
            sheet = get_object_or_404(SheetMusic, id=score_id)
            proyecto, created = MusicalProject.objects.get_or_create(
                user=request.user,
                sheet_music=sheet,
                defaults={'status': 'ACTIVE'}
            )
            return JsonResponse({'status': 'success', 'project_id': proyecto.id})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error'}, status=400)

# @csrf_exempt deliberado, NO un descuido: se llama exclusivamente vía
# navigator.sendBeacon() en el handler de beforeunload de biblioteca_play.html, que por
# diseño del navegador no permite mandar headers custom (no hay forma de adjuntar
# X-CSRFToken a un sendBeacon). Impacto de un CSRF forjado acá es bajo -- la vista ya
# filtra user=request.user (get_object_or_404 más abajo), así que lo peor que logra un
# atacante es pisar el last_measure/last_tempo del propio proyecto de la víctima; no
# hay alcance a datos de otro usuario ni gasto de crédito. Mismo criterio que
# log_study_session -- ver el comentario ahí para la decisión completa.
@csrf_exempt
@login_required
def api_update_project_state(request, project_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            from .models import MusicalProject
            proyecto = get_object_or_404(MusicalProject, id=project_id, user=request.user)
            if 'last_measure' in data:
                proyecto.last_measure = int(data['last_measure'])
            if 'last_tempo' in data:
                proyecto.last_tempo = int(data['last_tempo'])
            proyecto.save()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def api_update_project_section(request, project_id):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            from .models import MusicalProject, ProjectSection
            proyecto = get_object_or_404(MusicalProject, id=project_id, user=request.user)
            start_m = int(data.get('start_measure', 1))
            end_m = int(data.get('end_measure', 1))
            status = data.get('status', 'IN_PROGRESS')
            
            section, created = ProjectSection.objects.update_or_create(
                project=proyecto, start_measure=start_m, end_measure=end_m,
                defaults={'status': status}
            )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def midi_trainer_hub(request):
    return render(request, 'trainer/midi_hub.html')

@login_required
def midi_game_chords(request):
    return render(request, 'trainer/midi_game_chords.html')

@login_required
def api_log_midi_game(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            from .models import MidiGameSession, MidiChordStat
            
            # Registrar sesión
            score = data.get('score', 0)
            xp = data.get('xp', 0)
            duration = data.get('duration_seconds', 0)
            game_type = data.get('game_type', 'chord_identification')
            
            MidiGameSession.objects.create(
                user=request.user,
                game_type=game_type,
                score=score,
                xp_earned=xp,
                duration_seconds=duration
            )

            # Actualizar stats de acordes
            stats = data.get('chord_stats', [])
            for stat in stats:
                chord_name = stat.get('chord')
                is_correct = stat.get('correct', False)
                time_ms = stat.get('time_ms', 0)
                
                c_stat, _ = MidiChordStat.objects.get_or_create(user=request.user, chord_name=chord_name)
                if is_correct:
                    c_stat.correct_count += 1
                else:
                    c_stat.incorrect_count += 1
                
                # Simple moving average for time
                total_plays = c_stat.correct_count + c_stat.incorrect_count
                c_stat.avg_response_time_ms = ((c_stat.avg_response_time_ms * (total_plays - 1)) + time_ms) // total_plays
                
                if c_stat.correct_count > 10 and (c_stat.correct_count / total_plays) > 0.8:
                    c_stat.is_mastered = True
                    c_stat.is_problematic = False
                elif c_stat.incorrect_count > 5 and (c_stat.incorrect_count / total_plays) > 0.5:
                    c_stat.is_mastered = False
                    c_stat.is_problematic = True
                    
                c_stat.save()

            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error'}, status=400)
