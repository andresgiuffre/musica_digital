from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('perfil/', views.perfil, name='perfil'),
    path('juego/notas/', views.trainer_notas, name='trainer_notas'),
    path('juego/intervalos/', views.trainer_intervalos, name='trainer_intervalos'),
    path('juego/intervalos-auditivos/', views.trainer_intervalos_auditivos, name='trainer_intervalos_auditivos'),
    path('juego/dictado-melodico/', views.trainer_dictado_melodico, name='trainer_dictado_melodico'),
    path('juego/lectura-musical/', views.trainer_lectura_musical, name='trainer_lectura_musical'),
    path('biblioteca/', views.biblioteca_list, name='biblioteca_list'),
    path('biblioteca/<int:score_id>/', views.biblioteca_play, name='biblioteca_play'),
    path('api/record_attempt/<str:game_slug>/', views.record_attempt, name='record_attempt'),
    path('api/toggle_favorite/<int:score_id>/', views.toggle_favorite, name='toggle_favorite'),
    path('api/log_study_session/', views.log_study_session, name='log_study_session'),
    path('register/', views.register, name='register'),
]
