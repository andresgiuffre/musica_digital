from django import template

register = template.Library()


@register.filter
def initials(user):
    """Iniciales para el avatar de la navbar 'Gabinete de estudio' (ej. 'Ana P.' -> 'AP').

    Usa first_name/last_name si están cargados; si no, cae a las dos primeras
    letras del username (o una sola si el username no da para más).
    """
    nombre = (getattr(user, 'first_name', '') or '').strip()
    apellido = (getattr(user, 'last_name', '') or '').strip()
    if nombre and apellido:
        return (nombre[0] + apellido[0]).upper()
    if nombre:
        return nombre[:2].upper()
    username = getattr(user, 'username', '') or ''
    return username[:2].upper() if username else '?'
