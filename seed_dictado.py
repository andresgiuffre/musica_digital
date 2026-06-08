import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from trainer.models import Game

Game.objects.get_or_create(
    slug='dictado-melodico',
    defaults={
        'name': 'Dictado Melódico',
        'description': 'Escucha una melodía de 3 notas y selecciona la secuencia correcta.',
        'order': 4,
        'recommended_accuracy': 70,
        'recommended_attempts': 20
    }
)

print("Game seeded.")
