import json
import math
from django.utils import timezone
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
    
    # Goals check for quick UI summary
    today_goals = UserDailyGoal.objects.filter(user=request.user, date=today)
    if not today_goals.exists():
        default_goal, _ = DailyGoal.objects.get_or_create(title="Práctica Diaria Básica", goal_type="TIME", target_value=15)
        try:
            from django.db import IntegrityError
            UserDailyGoal.objects.get_or_create(user=request.user, goal=default_goal, date=today)
        except IntegrityError:
            pass
        today_goals = UserDailyGoal.objects.filter(user=request.user, date=today)

    context = {
        'profile': profile,
        'today_goals': today_goals,
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
    
    # Progress and Study Sessions
    progress = get_user_progress(request.user)
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
        'progress': progress,
        'total_study_minutes': total_study_minutes,
        'today_goals': today_goals,
    }
    return render(request, 'trainer/perfil.html', context)

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
    score = get_object_or_404(SheetMusic, id=score_id)
    return render(request, 'trainer/biblioteca_play.html', {'score': score})


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
                'user_level': profile.user_level
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)
