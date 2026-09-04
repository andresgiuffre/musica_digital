import json
import logging
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.mail import mail_admins
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import EventoPago, Suscripcion, CompraIndividual, Plan
from .providers import (
    verificar_firma_mercadopago, verificar_firma_paypal, parsear_referencia,
    obtener_pago_mercadopago, obtener_preapproval_mercadopago, PagosNoConfiguradoError,
)

logger = logging.getLogger(__name__)

# Duración aproximada de un período de suscripción -- MercadoPago/PayPal no siempre
# devuelven la fecha exacta del próximo cobro en cada tipo de evento, así que se usa un
# mes calendario fijo (30 días) como aproximación razonable, consistente entre los dos
# proveedores. Si algún Plan real necesita otra cadencia (anual, etc.) esto debería
# volverse un campo de Plan en vez de una constante -- no hace falta hoy, ningún Plan
# definido todavía tiene otra cadencia.
DIAS_PERIODO_SUSCRIPCION = 30


def _alertar_fallo_webhook(proveedor, motivo, detalle):
    """
    mail_admins() envuelto en try/except -- un email roto (SMTP no configurado, ADMINS
    vacío, lo que sea) NUNCA debe poder romper la respuesta 200 del webhook. Ver
    "Visibilidad sobre webhooks fallidos" en el plan de esta sesión para la
    justificación completa: sin esto, un pago legítimo perdido por un bug propio de
    verificación de firma quedaría en silencio hasta que un usuario reclame.

    Sin rate-limiting deliberadamente por ahora -- revisión obligatoria a los 30 días de
    que este feature esté en producción (ver esa misma sección del plan y la nota
    pendiente en CLAUDE.md).
    """
    try:
        mail_admins(
            subject=f"[pagos] {proveedor}: {motivo}",
            message=detalle,
            fail_silently=False,
        )
    except Exception as e:
        logger.warning(f"pagos.webhooks: no se pudo enviar la alerta de mail_admins() -- {e}")


def _registrar_evento(usuario, proveedor, tipo_evento, id_evento_externo, payload, procesado_ok, requiere_atencion, detalle):
    EventoPago.objects.create(
        usuario=usuario, proveedor=proveedor, tipo_evento=tipo_evento,
        id_evento_externo=id_evento_externo, payload_raw=payload,
        procesado_ok=procesado_ok, requiere_atencion=requiere_atencion, detalle=detalle,
    )
    if requiere_atencion:
        _alertar_fallo_webhook(proveedor, detalle, json.dumps(payload, indent=2, ensure_ascii=False)[:2000])


def _ya_procesado(proveedor, tipo_evento, id_evento_externo):
    if not id_evento_externo:
        return False
    return EventoPago.objects.filter(
        proveedor=proveedor, tipo_evento=tipo_evento,
        id_evento_externo=id_evento_externo, procesado_ok=True,
    ).exists()


# ==============================================================================
# MercadoPago
# ==============================================================================

def _aplicar_evento_mercadopago(tipo_evento, id_evento_externo):
    """
    Devuelve (usuario, detalle). El webhook de MercadoPago en sí solo trae {type,
    data.id} -- hace falta consultar la API para status/external_reference/monto reales.
    Tipos manejados: 'payment' (compra de curso) y 'subscription_preapproval'
    (alta/pausa/baja de una suscripción). La renovación periódica de una suscripción
    llega vía 'subscription_authorized_payment' en MercadoPago -- no implementado acá
    todavía (ver nota en el plan de esta sesión: la forma exacta de estos eventos
    recurrentes hay que confirmarla contra el simulador de webhooks real de MercadoPago,
    no adivinarla sin acceso a esa herramienta). Por ahora, cada webhook 'authorized' de
    subscription_preapproval extiende el período -- cubre el alta inicial correctamente;
    revisar contra sandbox real antes de depender de esto para renovaciones periódicas.
    """
    if tipo_evento == 'payment':
        pago = obtener_pago_mercadopago(id_evento_externo)
        status = pago.get('status')
        referencia = pago.get('external_reference', '')
        usuario_id, tipo, codigo = parsear_referencia(referencia)
        if usuario_id is None:
            return None, f"payment {id_evento_externo}: referencia no reconocida ({referencia!r})"
        try:
            usuario = User.objects.get(pk=usuario_id)
        except User.DoesNotExist:
            return None, f"payment {id_evento_externo}: usuario {usuario_id} no existe"

        if tipo == 'curso' and status == 'approved':
            CompraIndividual.objects.get_or_create(
                usuario=usuario, curso_codigo=codigo,
                defaults={
                    'proveedor': 'mercadopago', 'id_externo': id_evento_externo,
                    'moneda': 'ARS', 'precio_pagado': pago.get('transaction_amount', 0),
                },
            )
            return usuario, f"CompraIndividual creada/existente: {codigo}"
        return usuario, f"payment {id_evento_externo}: status={status}, sin efecto (no aprobado o no es compra de curso)"

    if tipo_evento == 'subscription_preapproval':
        preap = obtener_preapproval_mercadopago(id_evento_externo)
        status = preap.get('status')  # authorized/paused/cancelled
        referencia = preap.get('external_reference', '')
        usuario_id, tipo, codigo = parsear_referencia(referencia)
        if usuario_id is None or tipo != 'plan':
            return None, f"preapproval {id_evento_externo}: referencia no reconocida ({referencia!r})"
        try:
            usuario = User.objects.get(pk=usuario_id)
        except User.DoesNotExist:
            return None, f"preapproval {id_evento_externo}: usuario {usuario_id} no existe"
        try:
            plan = Plan.objects.get(codigo=codigo)
        except Plan.DoesNotExist:
            return usuario, f"preapproval {id_evento_externo}: plan {codigo!r} no existe"

        if status == 'authorized':
            ahora = timezone.now()
            Suscripcion.objects.update_or_create(
                id_externo=id_evento_externo,
                defaults={
                    'usuario': usuario, 'plan': plan, 'proveedor': 'mercadopago',
                    'estado': 'activa', 'moneda': 'ARS',
                    'precio_pagado': preap.get('auto_recurring', {}).get('transaction_amount') or plan.precio_ars,
                    'fecha_inicio': ahora,
                    'fecha_fin_periodo_actual': ahora + timedelta(days=DIAS_PERIODO_SUSCRIPCION),
                },
            )
            return usuario, f"Suscripcion activada/renovada: {codigo}"
        if status == 'cancelled':
            # NO se toca fecha_fin_periodo_actual -- el acceso sigue vigente hasta que
            # esa fecha pase sola, cancelar no corta al instante (regla de negocio central
            # de este feature).
            Suscripcion.objects.filter(id_externo=id_evento_externo).update(estado='cancelada', cancelada_en=timezone.now())
            return usuario, f"Suscripcion cancelada: {codigo}"
        if status == 'paused':
            # Tampoco se toca fecha_fin_periodo_actual -- un pago fallido no recorta un
            # período ya pagado, solo impide que se extienda en la próxima renovación.
            Suscripcion.objects.filter(id_externo=id_evento_externo).update(estado='pago_fallido')
            return usuario, f"Suscripcion pausada (pago fallido): {codigo}"
        return usuario, f"preapproval {id_evento_externo}: status={status}, sin acción definida"

    return None, f"tipo_evento no manejado: {tipo_evento}"


# @csrf_exempt deliberado, NO un descuido -- pero con una justificación DISTINTA a los
# dos @csrf_exempt existentes en trainer/views.py (log_study_session/
# api_update_project_state, justificados por navigator.sendBeacon() no poder mandar
# headers custom). Acá no hay sesión de Django en absoluto -- este endpoint lo llama el
# servidor de MercadoPago, no un navegador con cookies de sesión, así que no hay token
# CSRF que adjuntar ni forma de que lo haya. El control compensatorio es la verificación
# de firma HMAC (ver verificar_firma_mercadopago() en pagos/providers.py) antes de tocar
# cualquier fila de Suscripcion/CompraIndividual -- una request sin firma válida se
# loguea en EventoPago (procesado_ok=False, requiere_atencion=True) y no aplica ningún
# efecto. Blast radius de una request forjada que pasara la firma sería idéntico al de
# una request legítima del proveedor (por diseño, no hay forma de distinguirlas) -- por
# eso la firma, no el CSRF token, es lo que realmente protege este endpoint.
@csrf_exempt
@require_POST
def webhook_mercadopago(request):
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        payload = {}

    tipo_evento = payload.get('type', 'desconocido')
    id_evento_externo = str(payload.get('data', {}).get('id', ''))

    if not verificar_firma_mercadopago(request):
        _registrar_evento(None, 'mercadopago', tipo_evento, id_evento_externo, payload, False, True, "firma inválida")
        # Siempre 200 -- ver "Decisiones abiertas" en el plan de esta sesión: un 4xx/5xx
        # dispara reintentos infinitos del proveedor sobre algo que nunca va a pasar a
        # válido. Trade-off reconocido: si esto rechaza un payload LEGÍTIMO por un bug
        # propio, ese evento se pierde salvo por este EventoPago -- mitigado por la
        # alerta de mail_admins() que _registrar_evento() ya disparó arriba.
        return HttpResponse(status=200)

    if _ya_procesado('mercadopago', tipo_evento, id_evento_externo):
        _registrar_evento(None, 'mercadopago', tipo_evento, id_evento_externo, payload, False, False, "duplicado, ya procesado")
        return HttpResponse(status=200)

    try:
        usuario, detalle = _aplicar_evento_mercadopago(tipo_evento, id_evento_externo)
        _registrar_evento(usuario, 'mercadopago', tipo_evento, id_evento_externo, payload, True, False, detalle)
    except PagosNoConfiguradoError as e:
        _registrar_evento(None, 'mercadopago', tipo_evento, id_evento_externo, payload, False, True, f"proveedor no configurado: {e}")
    except Exception as e:
        logger.exception("pagos.webhooks: error inesperado aplicando evento de MercadoPago")
        _registrar_evento(None, 'mercadopago', tipo_evento, id_evento_externo, payload, False, True, f"error inesperado: {e}")

    return HttpResponse(status=200)


# ==============================================================================
# PayPal
# ==============================================================================

def _aplicar_evento_paypal(event_type, resource):
    """
    Devuelve (usuario, detalle). A diferencia de MercadoPago, PayPal manda el recurso
    completo dentro del propio webhook (resource) -- no hace falta una consulta aparte a
    su API para la mayoría de los tipos de evento manejados acá.

    Tipos manejados: PAYMENT.CAPTURE.COMPLETED (confirma la compra de un curso, ver
    capturar_orden_paypal() en pagos/providers.py y checkout_retorno() en
    pagos/views.py -- el disparo de la captura es síncrono en el retorno del usuario,
    pero el ACCESO se otorga acá, en la confirmación server-to-server), y
    BILLING.SUBSCRIPTION.ACTIVATED / .CANCELLED / .PAYMENT.FAILED. La renovación
    periódica exacta (nombre de evento para cada cobro recurrente ya activo) queda
    pendiente de confirmar contra el simulador de webhooks real de PayPal -- misma
    salvedad que en _aplicar_evento_mercadopago().
    """
    referencia = resource.get('custom_id', '')
    usuario_id, tipo, codigo = parsear_referencia(referencia)
    if usuario_id is None:
        return None, f"{event_type}: referencia no reconocida ({referencia!r})"
    try:
        usuario = User.objects.get(pk=usuario_id)
    except User.DoesNotExist:
        return None, f"{event_type}: usuario {usuario_id} no existe"

    if event_type == 'PAYMENT.CAPTURE.COMPLETED' and tipo == 'curso':
        monto = resource.get('amount', {}).get('value', 0)
        CompraIndividual.objects.get_or_create(
            usuario=usuario, curso_codigo=codigo,
            defaults={
                'proveedor': 'paypal', 'id_externo': resource.get('id', ''),
                'moneda': 'USD', 'precio_pagado': monto,
            },
        )
        return usuario, f"CompraIndividual creada/existente: {codigo}"

    if tipo == 'plan':
        try:
            plan = Plan.objects.get(codigo=codigo)
        except Plan.DoesNotExist:
            return usuario, f"{event_type}: plan {codigo!r} no existe"

        id_externo_suscripcion = resource.get('id', '')
        if event_type == 'BILLING.SUBSCRIPTION.ACTIVATED':
            ahora = timezone.now()
            Suscripcion.objects.update_or_create(
                id_externo=id_externo_suscripcion,
                defaults={
                    'usuario': usuario, 'plan': plan, 'proveedor': 'paypal',
                    'estado': 'activa', 'moneda': 'USD', 'precio_pagado': plan.precio_usd,
                    'fecha_inicio': ahora,
                    'fecha_fin_periodo_actual': ahora + timedelta(days=DIAS_PERIODO_SUSCRIPCION),
                },
            )
            return usuario, f"Suscripcion activada: {codigo}"
        if event_type == 'BILLING.SUBSCRIPTION.CANCELLED':
            Suscripcion.objects.filter(id_externo=id_externo_suscripcion).update(estado='cancelada', cancelada_en=timezone.now())
            return usuario, f"Suscripcion cancelada: {codigo}"
        if event_type == 'BILLING.SUBSCRIPTION.PAYMENT.FAILED':
            Suscripcion.objects.filter(id_externo=id_externo_suscripcion).update(estado='pago_fallido')
            return usuario, f"Suscripcion con pago fallido: {codigo}"

    return usuario, f"tipo_evento no manejado: {event_type}"


# Ver el comentario largo sobre @csrf_exempt arriba de webhook_mercadopago -- misma
# justificación (firma como control compensatorio, no sendBeacon), acá verificada
# server-to-server contra la propia API de PayPal en vez de un HMAC local (ver
# verificar_firma_paypal() en pagos/providers.py).
@csrf_exempt
@require_POST
def webhook_paypal(request):
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        payload = {}

    tipo_evento = payload.get('event_type', 'desconocido')
    id_evento_externo = str(payload.get('id', ''))
    resource = payload.get('resource', {})

    if not verificar_firma_paypal(request, payload):
        _registrar_evento(None, 'paypal', tipo_evento, id_evento_externo, payload, False, True, "firma inválida")
        return HttpResponse(status=200)

    if _ya_procesado('paypal', tipo_evento, id_evento_externo):
        _registrar_evento(None, 'paypal', tipo_evento, id_evento_externo, payload, False, False, "duplicado, ya procesado")
        return HttpResponse(status=200)

    try:
        usuario, detalle = _aplicar_evento_paypal(tipo_evento, resource)
        _registrar_evento(usuario, 'paypal', tipo_evento, id_evento_externo, payload, True, False, detalle)
    except PagosNoConfiguradoError as e:
        _registrar_evento(None, 'paypal', tipo_evento, id_evento_externo, payload, False, True, f"proveedor no configurado: {e}")
    except Exception as e:
        logger.exception("pagos.webhooks: error inesperado aplicando evento de PayPal")
        _registrar_evento(None, 'paypal', tipo_evento, id_evento_externo, payload, False, True, f"error inesperado: {e}")

    return HttpResponse(status=200)
