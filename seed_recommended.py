import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from trainer.models import Game

g1 = Game.objects.get(slug='notas')
g1.recommended_accuracy = 70
g1.recommended_attempts = 10
g1.save()

g2 = Game.objects.get(slug='intervalos')
g2.recommended_accuracy = 75
g2.recommended_attempts = 20
g2.save()

g3 = Game.objects.get(slug='intervalos-auditivos')
g3.recommended_accuracy = 70
g3.recommended_attempts = 20
g3.save()

print("Recommended goals seeded.")
