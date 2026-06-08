import json
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Exercise, Score, Attempt

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
    score, created = Score.objects.get_or_create(user=request.user)
    context = {
        'score': score
    }
    return render(request, 'trainer/dashboard.html', context)

@login_required
def trainer(request):
    # Retrieve or create default exercise for MVP
    exercise, _ = Exercise.objects.get_or_create(
        name='Notas Clave de Sol - Nivel 1',
        defaults={
            'clef': 'treble',
            'min_note': 'C4',
            'max_note': 'G5'
        }
    )
    score, _ = Score.objects.get_or_create(user=request.user)
    context = {
        'exercise': exercise,
        'score': score
    }
    return render(request, 'trainer/trainer.html', context)

@login_required
@csrf_exempt
def record_attempt(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            presented_note = data.get('presented_note')
            guessed_note = data.get('guessed_note')
            is_correct = data.get('is_correct')
            response_time_ms = data.get('response_time_ms')
            
            # Save attempt
            Attempt.objects.create(
                user=request.user,
                presented_note=presented_note,
                guessed_note=guessed_note,
                is_correct=is_correct,
                response_time_ms=response_time_ms
            )

            # Update score
            score = Score.objects.get(user=request.user)
            score.total_answers += 1
            if is_correct:
                score.correct_answers += 1
                score.current_streak += 1
                score.total_points += 10 + (score.current_streak * 2) # Bonus for streak
                if score.current_streak > score.max_streak:
                    score.max_streak = score.current_streak
            else:
                score.current_streak = 0
            
            # Level up logic (simple for MVP)
            if score.correct_answers > 0 and score.correct_answers % 20 == 0:
                if is_correct: # Only level up on a correct answer that hits the threshold
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
