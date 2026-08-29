# Paso 1 de 2 (i18n, Game.name_en): solo esquema, campo en blanco por default
# -- 0024 lo puebla con las traducciones reales de los Game existentes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trainer', '0022_curso_unique_together'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='name_en',
            field=models.CharField(blank=True, default='', help_text='Nombre en inglés. Si queda vacío, se muestra el español (name) también con idioma inglés activo.', max_length=100),
            preserve_default=False,
        ),
    ]
