# Paso 2 de 3 (i18n Fase 1) -- migración de DATOS, no solo de esquema.
# Para cada Curso preexistente (creado antes de que codigo/idioma
# existieran): genera codigo=slugify(nombre), desambiguando con sufijo
# -2/-3/... si dos cursos existentes colisionan dentro del mismo idioma;
# fija idioma='es' explícito (aunque el default de columna de 0020 ya sea
# 'es' -- pedido explícito: migración de datos real, no solo confiar en el
# default de columna, para que quede una traza clara de qué pasó acá).

from django.db import migrations
from django.utils.text import slugify


def poblar_codigo(apps, schema_editor):
    Curso = apps.get_model('trainer', 'Curso')
    codigos_usados_por_idioma = {}

    for curso in Curso.objects.all().order_by('id'):
        idioma = curso.idioma or 'es'
        usados = codigos_usados_por_idioma.setdefault(idioma, set())

        base = slugify(curso.nombre) or 'curso'
        codigo = base
        sufijo = 2
        while codigo in usados:
            codigo = f"{base}-{sufijo}"
            sufijo += 1
        usados.add(codigo)

        curso.codigo = codigo
        curso.idioma = idioma
        curso.save(update_fields=['codigo', 'idioma'])


def revertir(apps, schema_editor):
    # No hay nada sensato que "deshacer" acá -- volver codigo a '' dejaría
    # la tabla en el mismo estado inconsistente que 0020 ya prohibía antes
    # de que existiera el constraint de unicidad. No-op intencional.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('trainer', '0020_curso_idioma_codigo'),
    ]

    operations = [
        migrations.RunPython(poblar_codigo, revertir),
    ]
