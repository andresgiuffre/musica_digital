import os
import logging

logger = logging.getLogger(__name__)

# Ver plan de la sesión que introdujo esto para la justificación completa: GeoLite2
# offline (.mmdb local + paquete geoip2), no una API de lookup en vivo -- evita depender
# de salida a internet en el hosting (PythonAnywhere restringe esto en tiers bajos) y no
# agrega latencia/dependencia externa en la página de checkout. El archivo .mmdb vive
# FUERA del repo (mismo patrón que SCORE_FILE_ENCRYPTION_KEY: se genera/descarga una vez,
# nunca se commitea), la ruta se lee de GEOIP_DB_PATH. Si falta el archivo, o el lookup
# falla por cualquier motivo (IP privada/local en dev, IP malformada, archivo corrupto),
# resolver_region() cae directo al selector manual sin crashear -- geoip acá es
# puramente una sugerencia de default, nunca un gate.
_DB_PATH_ENV = 'GEOIP_DB_PATH'

PAISES_ARS = {'AR'}


def _obtener_ip_cliente(request):
    # X-Forwarded-For: PythonAnywhere termina TLS en su propio proxy (ver
    # SECURE_PROXY_SSL_HEADER en config/settings.py, mismo motivo) -- REMOTE_ADDR sin
    # esto sería la IP del proxy, no la del usuario real.
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _pais_desde_ip(ip):
    db_path = os.environ.get(_DB_PATH_ENV)
    if not db_path or not os.path.isfile(db_path):
        return None
    try:
        import geoip2.database
        import geoip2.errors
    except ImportError:
        logger.warning("pagos.geo: paquete geoip2 no instalado, se omite la detección automática de región.")
        return None
    try:
        with geoip2.database.Reader(db_path) as reader:
            return reader.country(ip).country.iso_code
    except (geoip2.errors.AddressNotFoundError, ValueError, OSError) as e:
        logger.info(f"pagos.geo: no se pudo resolver país para IP -- {e}")
        return None


def resolver_region(request):
    """
    Devuelve (region_code, moneda, proveedor) para precargar el checkout -- SIEMPRE una
    sugerencia, nunca un gate: el checkout debe ofrecer igual la opción manual de elegir
    el otro proveedor/moneda (un usuario detrás de VPN/proxy corporativo/viajando no debe
    quedar nunca bloqueado por una mala detección).

    Override manual: si la request trae ?region=AR o ?region=OTHER (querystring en
    checkout_iniciar_*, ver pagos/views.py), se respeta eso directo sin consultar geoip.
    """
    override = request.GET.get('region')
    if override == 'AR':
        return 'AR', 'ARS', 'mercadopago'
    if override == 'OTHER':
        return 'OTHER', 'USD', 'paypal'

    ip = _obtener_ip_cliente(request)
    pais = _pais_desde_ip(ip) if ip else None

    if pais in PAISES_ARS:
        return 'AR', 'ARS', 'mercadopago'
    # Default genérico (incluye "no se pudo determinar") -- USD/PayPal, no ARS/MercadoPago,
    # porque MercadoPago solo es correcto para Argentina específicamente mientras que
    # USD/PayPal es la opción razonable para "cualquier otro lugar, incluido desconocido".
    return 'OTHER', 'USD', 'paypal'
