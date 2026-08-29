# Paso 1 de 3 (i18n Fase 1) -- solo esquema, sin constraint de unicidad
# todavía. codigo lleva un default transitorio ('') únicamente para poder
# agregar la columna sobre filas existentes; 0021 lo puebla con valores
# reales y 0022 recién ahí agrega unique_together -- separado a propósito
# porque aplicar la constraint ya en este paso rompería si hay más de un
# Curso preexistente (todos recibirían el mismo codigo en blanco a la vez).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trainer', '0019_curso_grado_tema_bloquecontenido'),
    ]

    operations = [
        migrations.AddField(
            model_name='curso',
            name='idioma',
            field=models.CharField(choices=[('es', 'Español'), ('en', 'English')], default='es', max_length=2),
        ),
        migrations.AddField(
            model_name='curso',
            name='codigo',
            field=models.SlugField(default='', help_text=(
                "Identificador estable compartido entre versiones de idioma del mismo curso "
                "(ej. 'teoria-musical'). NO se traduce -- solo agrupa variantes de idioma "
                "del mismo curso entre sí."
            ), max_length=140),
            preserve_default=False,
        ),
    ]
