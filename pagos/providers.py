import os
import hmac
import hashlib
import logging

import requests

logger = logging.getLogger(__name__)


class PagosNoConfiguradoError(Exception):
    """
    Falta una credencial de proveedor de pago (MercadoPago/PayPal) para el modo actual
    (sandbox/prod, ver PAGOS_MODO). A diferencia de DJANGO_SECRET_KEY/
    SCORE_FILE_ENCRYPTION_KEY (que crashean al importar config/settings.py), estas
    credenciales se chequean recién acá, al momento de uso -- nadie debería necesitar
    credenciales de pago (ni sandbox) para correr runserver/test/editar un Curso en el
    admin. Mismo patrón que ANTHROPIC_TEST_API_KEY en trainer/views.py. Los callers
    (pagos/views.py, pagos/webhooks.py) atrapan esto y degradan sin crashear: checkout
    muestra "pagos no disponible", el webhook loguea un EventoPago y responde 200 igual.
    """
    pass


def _modo_actual():
    return os.environ.get('PAGOS_MODO', 'sandbox').upper()


def _get_env(nombre_base):
    modo = _modo_actual()
    valor = os.environ.get(f'{nombre_base}_{modo}')
    if not valor:
        raise PagosNoConfiguradoError(f"Falta la variable de entorno {nombre_base}_{modo}.")
    return valor


# ==============================================================================
# MercadoPago
# ==============================================================================

def _mercadopago_sdk():
    import mercadopago
    access_token = _get_env('MERCADOPAGO_ACCESS_TOKEN')
    return mercadopago.SDK(access_token)


def _referencia(usuario_id, tipo, codigo):
    """
    Formato compartido de external_reference (MercadoPago) / custom_id (PayPal):
    "user:<id>|<tipo>:<codigo>" -- ambos proveedores devuelven este campo tal cual en su
    webhook (MercadoPago dentro del recurso que hay que consultar aparte, PayPal directo
    en el payload), así que es la forma más simple de que el webhook sepa a qué User
    LOCAL corresponde un pago sin necesitar una tabla aparte de "checkouts pendientes".
    """
    return f"user:{usuario_id}|{tipo}:{codigo}"


def parsear_referencia(referencia):
    """Inverso de _referencia() -- devuelve (usuario_id, tipo, codigo) o (None, None, None)
    si el formato no matchea (referencia vacía/corrupta/de otro origen)."""
    try:
        parte_user, parte_resto = referencia.split('|', 1)
        _, usuario_id = parte_user.split(':', 1)
        tipo, codigo = parte_resto.split(':', 1)
        return int(usuario_id), tipo, codigo
    except (ValueError, AttributeError):
        return None, None, None


def crear_checkout_mercadopago_curso(usuario_id, curso_codigo, titulo, precio_ars, back_url):
    """
    Crea una Preference (checkout de pago único) para la compra de un curso. Devuelve la
    URL a la que redirigir al usuario (init_point). external_reference lleva
    usuario_id+curso_codigo (ver _referencia) -- así el webhook, al recibir la
    confirmación de pago, sabe a qué usuario/curso corresponde sin necesitar una tabla
    aparte de checkouts pendientes.
    """
    sdk = _mercadopago_sdk()
    resultado = sdk.preference().create({
        "items": [{
            "title": titulo,
            "quantity": 1,
            "currency_id": "ARS",
            "unit_price": float(precio_ars),
        }],
        "external_reference": _referencia(usuario_id, 'curso', curso_codigo),
        "back_urls": {"success": back_url, "pending": back_url, "failure": back_url},
        "auto_return": "approved",
    })
    return resultado["response"]["init_point"]


def crear_checkout_mercadopago_plan(usuario_id, plan_codigo, plan_id_preaprobacion, payer_email, back_url):
    """
    Crea una Preapproval (suscripción recurrente) a partir de un plan ya configurado del
    lado de MercadoPago (plan_id_preaprobacion -- se crea una vez en el dashboard/API de
    MercadoPago por cada Plan de este sitio, no en cada checkout). Devuelve init_point.
    external_reference lleva usuario_id+plan_codigo, mismo motivo que en el checkout de
    curso.
    """
    sdk = _mercadopago_sdk()
    resultado = sdk.preapproval().create({
        "preapproval_plan_id": plan_id_preaprobacion,
        "external_reference": _referencia(usuario_id, 'plan', plan_codigo),
        "payer_email": payer_email,
        "back_url": back_url,
    })
    return resultado["response"]["init_point"]


def obtener_pago_mercadopago(payment_id):
    """El webhook de pagos de MercadoPago solo trae {type, data.id} -- hace falta esta
    consulta aparte para saber status/external_reference/monto reales del pago."""
    sdk = _mercadopago_sdk()
    return sdk.payment().get(payment_id)["response"]


def obtener_preapproval_mercadopago(preapproval_id):
    """Igual que obtener_pago_mercadopago() pero para eventos de tipo
    subscription_preapproval (altas/bajas/cambios de una Preapproval)."""
    sdk = _mercadopago_sdk()
    return sdk.preapproval().get(preapproval_id)["response"]


def verificar_firma_mercadopago(request):
    """
    Verifica el header x-signature de un webhook de MercadoPago (Webhooks v2).
    Formato: "ts=<unix_ts>,v1=<hmac_hex>". Manifest a firmar:
    "id:<data.id>;request-id:<x-request-id>;ts:<ts>;" -- HMAC-SHA256 contra el webhook
    secret configurado en el dashboard de MercadoPago (MERCADOPAGO_WEBHOOK_SECRET_*, NO
    el access token). Comparación con hmac.compare_digest (constant-time), nunca ==.

    Nunca levanta excepción -- cualquier header ausente/malformado, o secret no
    configurado, resuelve a False (el caller decide qué hacer con eso, ver
    pagos/webhooks.py).
    """
    try:
        secret = _get_env('MERCADOPAGO_WEBHOOK_SECRET')
    except PagosNoConfiguradoError:
        return False

    x_signature = request.headers.get('x-signature', '')
    x_request_id = request.headers.get('x-request-id', '')
    if not x_signature or not x_request_id:
        return False

    partes = {}
    for parte in x_signature.split(','):
        if '=' in parte:
            clave, _, valor = parte.partition('=')
            partes[clave.strip()] = valor.strip()
    ts = partes.get('ts')
    v1 = partes.get('v1')
    if not ts or not v1:
        return False

    data_id = request.GET.get('data.id', '')
    if not data_id:
        try:
            import json
            data_id = json.loads(request.body or b'{}').get('data', {}).get('id', '')
        except (ValueError, AttributeError):
            data_id = ''

    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    firma_calculada = hmac.new(secret.encode('utf-8'), manifest.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(firma_calculada, v1)


# ==============================================================================
# PayPal
# ==============================================================================

def _paypal_base_url():
    return 'https://api-m.sandbox.paypal.com' if _modo_actual() == 'SANDBOX' else 'https://api-m.paypal.com'


def _paypal_access_token():
    client_id = _get_env('PAYPAL_CLIENT_ID')
    client_secret = _get_env('PAYPAL_CLIENT_SECRET')
    resp = requests.post(
        f'{_paypal_base_url()}/v1/oauth2/token',
        auth=(client_id, client_secret),
        data={'grant_type': 'client_credentials'},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()['access_token']


def crear_checkout_paypal_curso(usuario_id, curso_codigo, titulo, precio_usd, back_url):
    """
    Crea una Order (pago único, intent=CAPTURE) vía la Orders API v2 de PayPal. Devuelve
    la URL de aprobación (link rel="approve") a la que redirigir al usuario.
    custom_id lleva usuario_id+curso_codigo (ver _referencia en la sección MercadoPago
    de este archivo) -- el webhook lo lee directo del payload al confirmar el pago (a
    diferencia de MercadoPago, PayPal sí manda el recurso completo en el webhook, no
    hace falta una consulta aparte).
    """
    token = _paypal_access_token()
    resp = requests.post(
        f'{_paypal_base_url()}/v2/checkout/orders',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json={
            "intent": "CAPTURE",
            "purchase_units": [{
                "custom_id": _referencia(usuario_id, 'curso', curso_codigo),
                "description": titulo,
                "amount": {"currency_code": "USD", "value": f"{precio_usd:.2f}"},
            }],
            "application_context": {"return_url": back_url, "cancel_url": back_url},
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    for link in data.get('links', []):
        if link.get('rel') == 'approve':
            return link['href']
    raise PagosNoConfiguradoError("PayPal no devolvió un link de aprobación para la Order creada.")


def crear_checkout_paypal_plan(usuario_id, plan_codigo, paypal_plan_id, back_url):
    """
    Crea una Subscription vía la Subscriptions API v1 de PayPal, a partir de un plan ya
    configurado del lado de PayPal (paypal_plan_id -- se crea una vez por cada Plan de
    este sitio, no en cada checkout). Devuelve la URL de aprobación.
    custom_id lleva usuario_id+plan_codigo, mismo motivo que en el checkout de curso.
    """
    token = _paypal_access_token()
    resp = requests.post(
        f'{_paypal_base_url()}/v1/billing/subscriptions',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json={
            "plan_id": paypal_plan_id,
            "custom_id": _referencia(usuario_id, 'plan', plan_codigo),
            "application_context": {"return_url": back_url, "cancel_url": back_url},
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    for link in data.get('links', []):
        if link.get('rel') == 'approve':
            return link['href']
    raise PagosNoConfiguradoError("PayPal no devolvió un link de aprobación para la Subscription creada.")


def capturar_orden_paypal(order_id):
    """
    Captura (efectiviza) una Order de PayPal ya aprobada por el usuario -- con
    intent=CAPTURE, la aprobación del usuario en el checkout de PayPal NO efectiviza el
    cobro sola, hace falta este POST aparte. Se llama desde checkout_retorno() (ver
    pagos/views.py) cuando el usuario vuelve del checkout de PayPal. El webhook
    PAYMENT.CAPTURE.COMPLETED (ver pagos/webhooks.py) es la confirmación real que crea
    CompraIndividual -- esta función solo dispara la captura, no otorga acceso por sí
    misma (misma disciplina que el resto del checkout: nunca confiar en el camino
    síncrono del navegador para otorgar acceso).
    """
    token = _paypal_access_token()
    resp = requests.post(
        f'{_paypal_base_url()}/v2/checkout/orders/{order_id}/capture',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def verificar_firma_paypal(request, payload):
    """
    Verifica un webhook de PayPal vía su API "Verify Webhook Signature"
    (POST /v1/notifications/verify-webhook-signature) -- a diferencia de MercadoPago, no
    es un HMAC que se recalcula localmente, es una llamada server-to-server contra la
    propia API de PayPal. Necesita los headers que PayPal manda con cada webhook
    (PAYPAL-TRANSMISSION-ID/TIME/CERT-URL/AUTH-ALGO/TRANSMISSION-SIG) más el webhook_id
    configurado para este endpoint (PAYPAL_WEBHOOK_ID_*, un objeto separado por
    sandbox/prod en el dashboard de PayPal).

    Nunca levanta excepción -- cualquier error de red/timeout/config faltante resuelve a
    False (tratar una verificación que no se pudo completar como "no verificado", nunca
    como válida por defecto).
    """
    try:
        webhook_id = _get_env('PAYPAL_WEBHOOK_ID')
        token = _paypal_access_token()
    except (PagosNoConfiguradoError, requests.RequestException) as e:
        logger.warning(f"pagos.providers: no se pudo preparar la verificación de firma de PayPal -- {e}")
        return False

    headers = request.headers
    requeridos = ('PAYPAL-TRANSMISSION-ID', 'PAYPAL-TRANSMISSION-TIME', 'PAYPAL-CERT-URL', 'PAYPAL-AUTH-ALGO', 'PAYPAL-TRANSMISSION-SIG')
    if not all(headers.get(h) for h in requeridos):
        return False

    try:
        resp = requests.post(
            f'{_paypal_base_url()}/v1/notifications/verify-webhook-signature',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={
                "transmission_id": headers.get('PAYPAL-TRANSMISSION-ID'),
                "transmission_time": headers.get('PAYPAL-TRANSMISSION-TIME'),
                "cert_url": headers.get('PAYPAL-CERT-URL'),
                "auth_algo": headers.get('PAYPAL-AUTH-ALGO'),
                "transmission_sig": headers.get('PAYPAL-TRANSMISSION-SIG'),
                "webhook_id": webhook_id,
                "webhook_event": payload,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('verification_status') == 'SUCCESS'
    except requests.RequestException as e:
        logger.warning(f"pagos.providers: fallo de red verificando firma de PayPal -- {e}")
        return False
