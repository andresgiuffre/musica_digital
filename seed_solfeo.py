import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from trainer.models import Game

Game.objects.get_or_create(
    slug='solfeo-ritmico',
    defaults={
        'name': 'Solfeo Rítmico',
        'description': 'Aprende a reconocer y seguir patrones rítmicos y pulsos.',
        'order': 5,
        'recommended_accuracy': 70,
        'recommended_attempts': 20
    }
)

print("Game solfeo-ritmico seeded.")
