import json
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
from django.http import JsonResponse, HttpResponse, StreamingHttpResponse
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

    context = {
        'profile': profile,
        'today_goals': today_goals,
        'recommendations': recommendations,
        'weekly_summary': weekly_summary,
        'calendar_data': json.dumps(calendar_data)
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
                "description": "Resumen general del estilo, textura y distribución orquestal de la obra completa."
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
                        }
                    },
                    "required": [
                        "rango_compases",
                        "analisis_cuerdas",
                        "analisis_maderas",
                        "analisis_metales_percusion",
                        "analisis_balance_y_fango",
                        "solucion_prosa",
                        "ediciones_sugeridas"
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
    """Convierte un nombre de music21 (ej. 'C#4', 'B-3') a solfeo español ('Do#4', 'Si-3')."""
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

    if all_pitches:
        pitch_min = min(all_pitches, key=lambda p: p.ps)
        pitch_max = max(all_pitches, key=lambda p: p.ps)
        ambito = f"{_a_solfeo(pitch_min.nameWithOctave)} a {_a_solfeo(pitch_max.nameWithOctave)}"
        ambito_min_ps = pitch_min.ps
        ambito_max_ps = pitch_max.ps
        clase_mas_frecuente = Counter(p.name for p in all_pitches).most_common(1)[0][0]
        nota_mas_frecuente = _a_solfeo(clase_mas_frecuente)
    else:
        ambito = "Sin notas"
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


def evaluar_viabilidad_instrumental(part_name, part):
    """
    Compara (con music21, sin IA) el ámbito realmente tocado por una parte contra un
    rango cómodo/práctico de referencia. Si el instrumento no matchea ninguna entrada
    conocida de RANGOS_COMODOS, no genera alertas — mejor ninguna alerta que una mal
    atribuida a un instrumento equivocado.
    """
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
        alertas.append({
            'instrumento': part_name,
            'compas': compas,
            'nota': _a_solfeo(p.nameWithOctave),
            'severidad': severidad,
            'mensaje': f"Atención: {part_name} {severidad} el registro agudo cómodo ({nombre_min} a {nombre_max}) alcanzando {_a_solfeo(p.nameWithOctave)} en el compás {compas}."
        })
    if candidatas_graves:
        ps, compas, p = min(candidatas_graves, key=lambda t: t[0])
        severidad = 'excede' if ps < comodo_min else 'roza'
        alertas.append({
            'instrumento': part_name,
            'compas': compas,
            'nota': _a_solfeo(p.nameWithOctave),
            'severidad': severidad,
            'mensaje': f"Atención: {part_name} {severidad} el registro grave cómodo ({nombre_min} a {nombre_max}) descendiendo a {_a_solfeo(p.nameWithOctave)} en el compás {compas}."
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


CONTEXTO_COMPASES = 4  # compases de contexto a mostrar antes/después del rango editado


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


@login_required
def orquestador_analizar(request):
    from .services import consumir_credito_analisis
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

        def generador_analisis():
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

                densidad_por_compas = calcular_densidad_por_compas(parts)

                analysis_data = {
                    'instruments': instrument_names,
                    'key_signature': key_str,
                    'time_signature': ts,
                    'tempo': tempo,
                    'measures_data': measures_data,
                    'estadisticas_por_instrumento': estadisticas_por_instrumento
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
                        for _event in stream:
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
                        final_data = tool_use_block.input
                        final_data['estadisticas_por_instrumento'] = estadisticas_por_instrumento
                        final_data['alertas_viabilidad'] = alertas_viabilidad
                        final_data['densidad_por_compas'] = densidad_por_compas
                        if version_de is not None:
                            final_data['comparacion_version_anterior'] = comparar_versiones(version_de, parts)
                        consumir_credito_analisis(profile)
                        profile.refresh_from_db()
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

        return StreamingHttpResponse(generador_analisis(), content_type='application/x-ndjson')

    from .models import ScoreAnalysis
    analisis_previos = ScoreAnalysis.objects.filter(user=request.user).order_by('-created_at')[:20]

    return render(request, 'trainer/orquestador_analizar.html', {
        'creditos_analisis': profile.creditos_analisis,
        'creditos_bonus': profile.creditos_bonus,
        'analisis_previos': analisis_previos,
    })


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
    tempo_bpm = tempos[0].number if tempos else 100

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


@csrf_exempt
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


@csrf_exempt
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

@login_required
def biblioteca_list(request):
    col_slug = request.GET.get('collection')
    favorites_only = request.GET.get('favorites') == 'true'

    scores = SheetMusic.objects.all().order_by('-created_at')
    
    if col_slug:
        scores = scores.filter(collections__slug=col_slug)
        
    if favorites_only:
        scores = scores.filter(favorited_by__user=request.user)

    collections = Collection.objects.all()
    user_favorites = Favorite.objects.filter(user=request.user).values_list('sheet_music_id', flat=True)
    user_progress = {
        p.sheet_music_id: p.completion_percentage 
        for p in SheetMusicProgress.objects.filter(user=request.user)
    }

    context = {
        'scores': scores,
        'collections': collections,
        'user_favorites': user_favorites,
        'user_progress': user_progress,
        'current_collection': col_slug,
        'favorites_only': favorites_only
    }
    return render(request, 'trainer/biblioteca_list.html', context)

@login_required
@csrf_exempt
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
@csrf_exempt
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

@csrf_exempt
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

@csrf_exempt
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

@csrf_exempt
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

@csrf_exempt
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

@csrf_exempt
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

@csrf_exempt
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

@csrf_exempt
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

@csrf_exempt
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
