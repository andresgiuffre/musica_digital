# Paso 2 de 2 (i18n, Game.description_en): traducción real de los 9 Game
# existentes, por slug.

from django.db import migrations

TRADUCCIONES = {
    'notas': 'Learn to quickly recognize notes on the staff.',
    'intervalos': 'Learn to identify the musical distance between two notes.',
    'intervalos-auditivos': 'Train your ear to recognize musical intervals.',
    'dictado-melodico': 'Listen to a short melody and recognize it among the options.',
    'lectura-musical': 'Learn to read progressive scores, procedurally generated with melodic sense.',
    'tiempos-fuertes-debiles': 'Identify the strong beat of each measure.',
    'sincopas-contratiempos': 'Recognize syncopated and off-beat rhythms.',
    'reconocimiento-acordes': 'Identify chords by ear.',
    'analisis-progresiones': 'Reconstruct chord progressions by ear.',
}


def poblar_description_en(apps, schema_editor):
    Game = apps.get_model('trainer', 'Game')
    for slug, description_en in TRADUCCIONES.items():
        Game.objects.filter(slug=slug).update(description_en=description_en)


def revertir(apps, schema_editor):
    Game = apps.get_model('trainer', 'Game')
    Game.objects.filter(slug__in=TRADUCCIONES.keys()).update(description_en='')


class Migration(migrations.Migration):

    dependencies = [
        ('trainer', '0025_game_description_en'),
    ]

    operations = [
        migrations.RunPython(poblar_description_en, revertir),
    ]
