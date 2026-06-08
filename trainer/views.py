import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
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

            return JsonResponse({
                'status': 'success',
                'current_streak': score.current_streak,
                'max_streak': score.max_streak,
                'total_points': score.total_points,
                'level': score.level,
                'accuracy': score.accuracy
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Invalid method'}, status=405)
