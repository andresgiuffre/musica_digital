# Paso 1 de 2 (i18n, Game.description_en) -- mismo patrón que name_en
# (0023/0024): esquema primero, datos después.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trainer', '0024_game_name_en_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='description_en',
            field=models.TextField(blank=True, default='', help_text='Descripción en inglés. Si queda vacío, se muestra el español (description) también con idioma inglés activo.'),
            preserve_default=False,
        ),
    ]
