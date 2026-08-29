# Paso 2 de 2 (i18n, Game.name_en): traducción real de los 9 Game existentes,
# por slug (no por id, para no depender del orden de creación).

from django.db import migrations

TRADUCCIONES = {
    'notas': 'Note Identification',
    'intervalos': 'Intervals',
    'intervalos-auditivos': 'Interval Ear Training',
    'dictado-melodico': 'Melodic Dictation',
    'lectura-musical': 'Sight Reading',
    'tiempos-fuertes-debiles': 'Strong and Weak Beats',
    'sincopas-contratiempos': 'Syncopation and Off-beats',
    'reconocimiento-acordes': 'Chord Recognition',
    'analisis-progresiones': 'Progression Analysis',
}


def poblar_name_en(apps, schema_editor):
    Game = apps.get_model('trainer', 'Game')
    for slug, name_en in TRADUCCIONES.items():
        Game.objects.filter(slug=slug).update(name_en=name_en)


def revertir(apps, schema_editor):
    Game = apps.get_model('trainer', 'Game')
    Game.objects.filter(slug__in=TRADUCCIONES.keys()).update(name_en='')


class Migration(migrations.Migration):

    dependencies = [
        ('trainer', '0023_game_name_en'),
    ]

    operations = [
        migrations.RunPython(poblar_name_en, revertir),
    ]
