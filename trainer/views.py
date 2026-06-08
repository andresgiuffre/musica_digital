import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Avg, Count, Q
from .models import Game, Score, Attempt

def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('dashboard')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})

@login_required
def dashboard(request):
    games = Game.objects.all()
    scores = Score.objects.filter(user=request.user)
    total_points = sum(s.total_points for s in scores)
    
    context = {
        'games': games,
        'total_points': total_points,
    }
    return render(request, 'trainer/dashboard.html', context)

@login_required
def trainer_notas(request):
    game = get_object_or_404(Game, slug='notas')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    context = {
        'game': game,
        'score': score
    }
    return render(request, 'trainer/trainer_notas.html', context)

@login_required
def trainer_intervalos(request):
    game = get_object_or_404(Game, slug='intervalos')
    score, _ = Score.objects.get_or_create(user=request.user, game=game)
    context = {
        'game': game,
        'score': score
    }
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
            if is_correct:
                score.correct_answers += 1
                score.current_streak += 1
                score.total_points += 10 + (score.current_streak * 2)
                if score.current_streak > score.max_streak:
                    score.max_streak = score.current_streak
            else:
                score.current_streak = 0
            
            if score.correct_answers > 0 and score.correct_answers % 20 == 0:
                if is_correct:
                    score.level += 1

            score.save()

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
                'hardest': stats_data['hardest']
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)
