import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from trainer.models import Game, Achievement

# Update Games
g1 = Game.objects.get(slug='notas')
g1.order = 1
g1.unlock_required_accuracy = 0
g1.unlock_required_attempts = 0
g1.save()

g2 = Game.objects.get(slug='intervalos')
g2.order = 2
g2.unlock_required_accuracy = 80
g2.unlock_required_attempts = 50
g2.save()

g3 = Game.objects.get(slug='intervalos-auditivos')
g3.order = 3
g3.unlock_required_accuracy = 75
g3.unlock_required_attempts = 50
g3.save()

# Seed Achievements
achievements = [
    {
        'slug': 'primer-paso',
        'name': 'Primer Paso',
        'description': 'Completar el primer ejercicio.',
        'icon': '🌱'
    },
    {
        'slug': '10-correctas',
        'name': '10 Correctas',
        'description': 'Responder correctamente 10 ejercicios.',
        'icon': '🎯'
    },
    {
        'slug': 'velocista',
        'name': 'Velocista',
        'description': 'Responder 20 ejercicios en una sesión.',
        'icon': '⚡'
    },
    {
        'slug': 'constancia',
        'name': 'Constancia',
        'description': 'Entrenar 7 días seguidos.',
        'icon': '🔥'
    },
    {
        'slug': 'maestro-notas',
        'name': 'Maestro de las Notas',
        'description': 'Completar Nivel 1.',
        'icon': '🎵'
    },
    {
        'slug': 'explorador-intervalos',
        'name': 'Explorador de Intervalos',
        'description': 'Completar Nivel 2.',
        'icon': '🔭'
    },
    {
        'slug': 'oido-entrenado',
        'name': 'Oído Entrenado',
        'description': 'Completar Nivel 3.',
        'icon': '👂'
    }
]

for a in achievements:
    obj, created = Achievement.objects.get_or_create(slug=a['slug'], defaults=a)
    if not created:
        for k, v in a.items():
            setattr(obj, k, v)
        obj.save()

print("Gamification seeding completed.")
