from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Sum, Avg
from .models import StudySession, SheetMusic, UserProfile

def get_weekly_summary(user):
    """
    Returns a summary of the user's study sessions over the last 7 days.
    """
    now = timezone.now()
    seven_days_ago = now - timedelta(days=7)
    
    sessions = StudySession.objects.filter(user=user, date__gte=seven_days_ago)
    
    total_sessions = sessions.count()
    total_duration = sessions.aggregate(Sum('duration_seconds'))['duration_seconds__sum'] or 0
    total_minutes = total_duration // 60
    
    unique_sheets = sessions.values('sheet_music').distinct().count()
    
    # We can also calculate how many different days the user studied this week
    unique_days = sessions.dates('date', 'day').count()
    
    return {
        'total_sessions': total_sessions,
        'total_minutes': total_minutes,
        'unique_sheets': unique_sheets,
        'unique_days': unique_days
    }

def get_study_recommendations(user):
    """
    Analyzes user activity and generates a list of actionable recommendations.
    """
    recommendations = []
    now = timezone.now()
    seven_days_ago = now - timedelta(days=7)
    
    # Check for abandoned sheet music
    # E.g. Sheet music played in the last 30 days but not in the last 7 days
    thirty_days_ago = now - timedelta(days=30)
    abandoned_sessions = StudySession.objects.filter(
        user=user, 
        date__gte=thirty_days_ago, 
        date__lt=seven_days_ago
    ).values_list('sheet_music__title', flat=True).distinct()
    
    for title in abandoned_sessions:
        # Verify it wasn't played in the last 7 days
        recent = StudySession.objects.filter(user=user, sheet_music__title=title, date__gte=seven_days_ago).exists()
        if not recent:
            recommendations.append({
                'type': 'reminder',
                'message': f"Hace más de 7 días que no practicas '{title}'."
            })
            break # Only show one reminder of this type to not overwhelm
            
    # Check for BPM progress opportunities
    recent_sessions = StudySession.objects.filter(user=user, date__gte=seven_days_ago)
    if recent_sessions.exists():
        # Find highest played count for a specific sheet this week
        most_played = recent_sessions.values('sheet_music__title').annotate(play_count_sum=Sum('play_count'), max_bpm=Sum('bpm_used')).order_by('-play_count_sum').first()
        if most_played and most_played['play_count_sum'] > 5:
            recommendations.append({
                'type': 'motivation',
                'message': f"Has repetido '{most_played['sheet_music__title']}' muchas veces. ¡Intenta subirle el tempo (BPM) hoy!"
            })
            
    if not recommendations:
        recommendations.append({
            'type': 'suggestion',
            'message': "Continúa así. Intenta enfocarte en pasajes lentos hoy."
        })
        
    return recommendations

def update_personal_records(user):
    """
    Recalculates and updates the user's personal records in UserProfile.
    This should be called after a session is logged.
    """
    profile, _ = UserProfile.objects.get_or_create(user=user)
    
    # Calculate max study time in a single day
    sessions_by_day = StudySession.objects.filter(user=user).values('date__date').annotate(
        daily_duration=Sum('duration_seconds'),
        daily_sessions=Count('id')
    ).order_by('-daily_duration')
    
    if sessions_by_day.exists():
        max_duration_record = sessions_by_day.first()
        profile.max_study_time_day = max_duration_record['daily_duration']
        
        # Max sessions
        max_sessions_record = sorted(sessions_by_day, key=lambda x: x['daily_sessions'], reverse=True)[0]
        profile.max_sessions_day = max_sessions_record['daily_sessions']
        
        profile.save()
        
    # Unlock Achievements
    from .models import UserAchievement, Achievement
    total_seconds = StudySession.objects.filter(user=user).aggregate(Sum('duration_seconds'))['duration_seconds__sum'] or 0
    total_hours = total_seconds / 3600
    total_minutes = total_seconds / 60
    
    achievements_to_check = []
    if total_minutes >= 30:
        achievements_to_check.append('30-minutos')
    if total_hours >= 5:
        achievements_to_check.append('5-horas')
    if total_hours >= 20:
        achievements_to_check.append('20-horas')
    if total_hours >= 50:
        achievements_to_check.append('50-horas')
    if total_hours >= 100:
        achievements_to_check.append('100-horas')
        
    if sessions_by_day.exists() and sessions_by_day.count() >= 365:
        achievements_to_check.append('365-dias')
        
    for slug in achievements_to_check:
        try:
            ach = Achievement.objects.get(slug=slug)
            UserAchievement.objects.get_or_create(user=user, achievement=ach)
        except Achievement.DoesNotExist:
            pass
