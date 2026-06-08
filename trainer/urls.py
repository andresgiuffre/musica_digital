from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('juego/notas/', views.trainer_notas, name='trainer_notas'),
    path('juego/intervalos/', views.trainer_intervalos, name='trainer_intervalos'),
    path('api/record_attempt/<str:game_slug>/', views.record_attempt, name='record_attempt'),
    path('register/', views.register, name='register'),
]
