from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('trainer/', views.trainer, name='trainer'),
    path('api/record_attempt/', views.record_attempt, name='record_attempt'),
    path('register/', views.register, name='register'),
]
