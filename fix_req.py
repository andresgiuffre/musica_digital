import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from trainer.models import Game

g1 = Game.objects.get(slug='notas')
g1.unlock_required_accuracy = 80
g1.unlock_required_attempts = 50
g1.save()

g2 = Game.objects.get(slug='intervalos')
g2.unlock_required_accuracy = 75
g2.unlock_required_attempts = 50
g2.save()

g3 = Game.objects.get(slug='intervalos-auditivos')
g3.unlock_required_accuracy = 70
g3.unlock_required_attempts = 50
g3.save()

print("Fixed completion requirements.")
