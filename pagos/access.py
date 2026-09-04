from django.utils import timezone


def tiene_acceso(usuario, feature=None, curso=None):
    """
    Única función de resolución de acceso del sitio para todo lo relacionado a
    pagos/suscripciones. Cualquier vista/template que necesite decidir si un usuario
    puede ver un curso o usar un feature pago DEBE pasar por acá -- nunca reimplementar
    esta lógica ad hoc ni leer Suscripcion.estado directamente (ver el help_text de ese
    campo en pagos/models.py: es solo diagnóstico, nunca la fuente de verdad de acceso).

    Orden de resolución cuando se pasa curso=... :
      1. ¿El curso es gratuito? (Curso.es_gratuito) -> True, sin mirar usuario.
      2. ¿El usuario lo compró individualmente? (CompraIndividual por curso.codigo, NO
         por curso.id -- Curso tiene una fila separada por idioma que comparte codigo,
         comprar un curso lo desbloquea sin importar el idioma activo).
      3. ¿El usuario tiene una suscripción vigente cuyo plan lo cubre? Vigente =
         fecha_fin_periodo_actual >= ahora (NUNCA estado=='activa'). Cubre =
         plan.incluye_todos_los_cursos es True.
      4. Si no se cumplió ninguna, False.

    Orden de resolución cuando se pasa feature=... (sin curso):
      1. ¿El usuario tiene una suscripción vigente cuyo plan incluye ese Feature (M2M
         Plan.features)? Misma definición de "vigente" que arriba.
      2. Si no, False. (No hay compra individual de Features sueltos en el diseño
         actual -- si eso cambia, agregar acá, no en otro lado.)

    Si se pasan feature Y curso juntos: se interpreta como AND de ambas resoluciones --
    hoy ningún callsite necesita este caso combinado, se deja documentado por si aparece.

    Devuelve siempre un bool. No levanta excepción por usuario anónimo/None -- un curso
    gratuito es accesible aunque usuario sea None/AnonymousUser (todas las vistas de
    cursos son @login_required hoy, así que en la práctica esto no se ejercita todavía;
    se diseña así para que la función sea correcta también si algún día se usa desde una
    vista pública).
    """
    if curso is not None and getattr(curso, 'es_gratuito', False):
        return True

    usuario_autenticado = usuario is not None and getattr(usuario, 'is_authenticated', False)
    if not usuario_autenticado:
        return False

    if curso is not None:
        from .models import CompraIndividual
        if CompraIndividual.objects.filter(usuario=usuario, curso_codigo=curso.codigo).exists():
            return True

    from .models import Suscripcion
    suscripcion_vigente = (
        Suscripcion.objects
        .filter(usuario=usuario, fecha_fin_periodo_actual__gte=timezone.now())
        .select_related('plan')
        .order_by('-fecha_fin_periodo_actual')
        .first()
    )
    if suscripcion_vigente is not None:
        if curso is not None and suscripcion_vigente.plan.incluye_todos_los_cursos:
            return True
        if feature is not None and suscripcion_vigente.plan.features.filter(pk=feature.pk).exists():
            return True

    return False


def tema_es_accesible(usuario, tema):
    """
    Chequeo de acceso a nivel Tema, separado de tiene_acceso() a propósito -- no un
    parámetro tema= extra ahí, para no mezclar "¿está desbloqueado el curso?" con
    "¿es este Tema puntual una muestra gratis dentro de un curso pago?" en una sola
    función con ramificaciones según qué kwargs se pasaron.

    Resolución:
      1. ¿El Tema es muestra gratuita? (Tema.es_muestra_gratuita) -> True, sin mirar
         si el Curso que lo contiene está comprado/incluido en el plan.
      2. Si no, delega en tiene_acceso(usuario, curso=tema.grado.curso).
    """
    if getattr(tema, 'es_muestra_gratuita', False):
        return True
    return tiene_acceso(usuario, curso=tema.grado.curso)
