from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('perfil/', views.perfil, name='perfil'),
    path('juego/notas/', views.trainer_notas, name='trainer_notas'),
    path('juego/intervalos/', views.trainer_intervalos, name='trainer_intervalos'),
    path('juego/intervalos-auditivos/', views.trainer_intervalos_auditivos, name='trainer_intervalos_auditivos'),
    path('juego/dictado-melodico/', views.trainer_dictado_melodico, name='trainer_dictado_melodico'),
    path('juego/solfeo-ritmico/', views.trainer_solfeo_ritmico, name='trainer_solfeo_ritmico'),
    path('api/record_attempt/<str:game_slug>/', views.record_attempt, name='record_attempt'),
    path('register/', views.register, name='register'),
]
