# Paso 3 de 3 (i18n Fase 1) -- recién acá se agrega la constraint de
# unicidad, garantizado sin colisiones por la migración de datos anterior
# (0021).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('trainer', '0021_curso_codigo_data'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='curso',
            unique_together={('codigo', 'idioma')},
        ),
    ]
