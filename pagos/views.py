from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .access import tiene_acceso
from .geo import resolver_region
from .models import Plan
from .providers import (
    PagosNoConfiguradoError,
    capturar_orden_paypal,
    crear_checkout_mercadopago_curso,
    crear_checkout_mercadopago_plan,
    crear_checkout_paypal_curso,
    crear_checkout_paypal_plan,
)


def _back_url(request):
    return request.build_absolute_uri(reverse('checkout_retorno'))


@login_required
def checkout_iniciar_plan(request, plan_codigo):
    """
    Arranca el flujo de pago para una suscripción a `plan`, eligiendo proveedor/moneda
    según pagos/geo.py:resolver_region() (geoip + override manual por ?region=).
    NO crea Suscripcion acá -- eso pasa solo cuando llega el webhook confirmando el pago
    (confiar en el retorno síncrono del navegador para otorgar acceso sería confiar en
    un valor que el usuario podría manipular).
    """
    plan = get_object_or_404(Plan, codigo=plan_codigo, activo=True)
    region_code, moneda, proveedor = resolver_region(request)
    back_url = _back_url(request)

    try:
        if proveedor == 'mercadopago':
            if not plan.mercadopago_plan_id:
                raise PagosNoConfiguradoError(f"Plan {plan.codigo!r} no tiene mercadopago_plan_id configurado.")
            url = crear_checkout_mercadopago_plan(
                request.user.id, plan.codigo, plan.mercadopago_plan_id, request.user.email, back_url,
            )
        else:
            if not plan.paypal_plan_id:
                raise PagosNoConfiguradoError(f"Plan {plan.codigo!r} no tiene paypal_plan_id configurado.")
            url = crear_checkout_paypal_plan(request.user.id, plan.codigo, plan.paypal_plan_id, back_url)
    except PagosNoConfiguradoError:
        return render(request, 'pagos/checkout_no_disponible.html', status=503)

    return redirect(url)


@login_required
def checkout_iniciar_curso(request, curso_codigo):
    """
    Arranca el flujo de pago para la compra individual de un curso (identificado por
    codigo, no por una fila puntual de Curso -- ver pagos/access.py). Toma nombre/precio
    de cualquier fila de Curso que comparta ese codigo (todas las variantes de idioma
    tienen el mismo precio -- si eso cambia, ajustar acá).
    """
    from trainer.models import Curso

    curso = get_object_or_404(Curso, codigo=curso_codigo, activo=True)
    if tiene_acceso(request.user, curso=curso):
        return redirect('curso_detail', curso.id)

    region_code, moneda, proveedor = resolver_region(request)
    back_url = _back_url(request)

    try:
        if proveedor == 'mercadopago':
            url = crear_checkout_mercadopago_curso(
                request.user.id, curso.codigo, curso.nombre, curso.precio_ars, back_url,
            )
        else:
            url = crear_checkout_paypal_curso(
                request.user.id, curso.codigo, curso.nombre, curso.precio_usd, back_url,
            )
    except PagosNoConfiguradoError:
        return render(request, 'pagos/checkout_no_disponible.html', status=503)

    return redirect(url)


@login_required
def checkout_retorno(request):
    """
    Página de aterrizaje post-pago -- PURAMENTE UX, nunca fuente de verdad de acceso: es
    un redirect de navegador con query params que el propio usuario podría manipular, no
    una confirmación server-to-server. El acceso real se otorga exclusivamente en los
    webhooks (ver pagos/webhooks.py), que pueden llegar unos segundos DESPUÉS de este
    retorno -- por eso esta vista consulta tiene_acceso() en el momento y muestra
    "confirmando tu pago" si todavía no reflejó el cambio, en vez de asumir éxito.

    Para PayPal con intent=CAPTURE específicamente: la aprobación del usuario no
    efectiviza el cobro sola, hace falta este POST de captura (ver capturar_orden_paypal
    en pagos/providers.py) -- dispara la captura, pero el acceso se otorga recién cuando
    llega el webhook PAYMENT.CAPTURE.COMPLETED confirmando que salió bien.
    """
    token = request.GET.get('token')  # PayPal Orders API: id de la Order en el query param 'token'
    if token:
        try:
            capturar_orden_paypal(token)
        except Exception:
            pass  # el webhook es la fuente de verdad -- un fallo acá solo afecta cuán rápido se refleja el acceso, no si se otorga

    return render(request, 'pagos/checkout_retorno.html')
