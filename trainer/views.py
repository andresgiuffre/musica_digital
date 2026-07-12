import json
import math
import os
from django.utils import timezone
from pydantic import BaseModel, Field
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Avg
from django.contrib import messages
from .models import Game, Score, Attempt, UserProfile, Achievement, UserAchievement, Piece, SheetMusic, Collection, Favorite, StudySession, SheetMusicProgress, DailyGoal, UserDailyGoal

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

class AlertaBalance(BaseModel):
    compas: str = Field(description="Número de compás o rango")
    problema: str = Field(description="Descripción técnica del problema (ej. 'Los metales graves en f van a tapar por completo la línea melódica de las maderas medias').")
    sugerencia: str = Field(description="Solución orquestal concreta (ej. 'Bajar la dinámica de trombones a mp, o duplicar la melodía con violas y cornos al unísono para darle más densidad y cuerpo frente al metal').")

class SugerenciaColorDoblaje(BaseModel):
    seccion: str = Field(description="Nombre del grupo de instrumentos o pasaje analizado")
    critica: str = Field(description="Análisis del color actual (ej. 'La melodía principal en flauta sola en el registro medio suena delgada para el carácter épico que busca el acompañamiento de cuerdas en staccato').")
    alternativas: str = Field(description="Proponer 2 combinaciones avanzadas de doblaje detallando el efecto psicológico de cada una (ej. 'Opción A: Doblar con Oboe al unísono para un color más penetrante y rústico. Opción B: Doblar con Violines I a la octava superior para darle brillo cinematográfico').")

class OrchestrationAnalysis(BaseModel):
    resumen_estilo: str = Field(description="Breve análisis de la textura general detectada (ej. homofónica, contrapuntística, masiva) y la distribución del mapa orquestal.")
    alertas_balance: list[AlertaBalance]
    sugerencias_color_y_doblaje: list[SugerenciaColorDoblaje]
    ejercicio_practico: str = Field(description="Un ejercicio o restricción compositiva personalizada basada en los errores del usuario para que aplique en su próximo compás (ej. 'Escribí los siguientes 8 compases usando únicamente combinaciones de maderas y cuerdas bajas, sin usar metales ni percusión, para entrenar el balance de texturas blandas').")

@login_required
def orquestador_analizar(request):
    if request.method == 'POST' and request.FILES.get('score_file'):
        score_file = request.FILES['score_file']
        name = request.POST.get('name', score_file.name)
        
        from .models import ScoreAnalysis
        analysis = ScoreAnalysis.objects.create(
            user=request.user,
            name=name,
            score_file=score_file
        )
        
        try:
            file_path = analysis.score_file.path
            score = music21.converter.parse(file_path)
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
            for part in parts:
                part_name = part.partName or "Instrumento Desconocido"
                measures = part.getElementsByClass(music21.stream.Measure)
                
                part_data = []
                for m in list(measures)[:8]:
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

            analysis_data = {
                'instruments': instrument_names,
                'key_signature': key_str,
                'time_signature': ts,
                'tempo': tempo,
                'measures_data': measures_data
            }
            
            if genai:
                client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
                
                system_instruction = (
                    "Sos un Maestro de Orquestación Clásica y Cinematográfica de élite, con décadas de experiencia "
                    "analizando partituras de compositores como John Williams, Ravel, Stravinsky, Jeremy Soule y Nobuo Uematsu. "
                    "Tu objetivo no es enseñar teoría básica (rango de instrumentos o qué es un ostinato), sino auditar el "
                    "CRITERIO de orquestación, el BALANCE de frecuencias y el COLOR de los doblajes.\n\n"
                    "Cuando recibas la estructura de datos de una pieza (instrumentos, notas por compás y dinámicas), "
                    "debés devolver un análisis crítico estructurado estrictamente en formato JSON."
                )
                
                prompt = f"Por favor, analiza la siguiente estructura de datos musicales:\n{json.dumps(analysis_data, ensure_ascii=False)}"
                
                response = client.models.generate_content(
                    model='models/gemini-2.0-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=OrchestrationAnalysis,
                        temperature=0.7,
                    ),
                )
                
                final_data = json.loads(response.text)
            else:
                final_data = {
                    "error": "El SDK de google-genai no está instalado.",
                    "raw_music_data": analysis_data
                }

            analysis.analysis_data = final_data
            analysis.save()
            
            from django.http import JsonResponse
            return JsonResponse({'status': 'success', 'data': final_data})
            
        except Exception as e:
            from django.http import JsonResponse
            return JsonResponse({'status': 'error', 'message': str(e)})

    return render(request, 'trainer/orquestador_analizar.html')

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
