from django.db import models
from django.contrib.auth.models import User

# Monedas/proveedores soportados -- solo dos de cada, cada proveedor liquida en UNA sola
# moneda (MercadoPago siempre ARS, PayPal siempre USD en este diseño), así que no se usa
# una librería de moneda genérica (django-money/py-moneyed) -- no hay conversión ni
# terceras monedas que justifiquen esa dependencia.
MONEDA_CHOICES = [
    ('ARS', 'ARS'),
    ('USD', 'USD'),
]

PROVEEDOR_CHOICES = [
    ('mercadopago', 'MercadoPago'),
    ('paypal', 'PayPal'),
]


class Feature(models.Model):
    codigo = models.SlugField(max_length=50, unique=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)

    class Meta:
        verbose_name = "Feature"
        verbose_name_plural = "Features"

    def __str__(self):
        return self.nombre


class Plan(models.Model):
    codigo = models.SlugField(max_length=50, unique=True)
    nombre = models.CharField(max_length=100)
    precio_ars = models.DecimalField(max_digits=10, decimal_places=2)
    precio_usd = models.DecimalField(max_digits=10, decimal_places=2)
    features = models.ManyToManyField(Feature, blank=True, related_name='planes')
    incluye_todos_los_cursos = models.BooleanField(default=False, help_text="Si está activo, este plan da acceso a TODOS los cursos pagos sin importar compra individual (ver pagos/access.py:tiene_acceso()).")
    activo = models.BooleanField(default=True, help_text="Solo los planes activos se ofrecen en checkout -- desactivar un plan NO corta el acceso de quienes ya están suscriptos a él (tiene_acceso() nunca mira este campo, solo Suscripcion.fecha_fin_periodo_actual). Mismo criterio que Curso.activo en trainer/models.py.")
    # IDs del "plan" ya creado del lado de cada proveedor -- las APIs de suscripciones de
    # MercadoPago (Preapproval) y PayPal (Billing Plans) necesitan referenciar un objeto
    # de plan pre-existente del lado del proveedor, no solo un precio suelto en cada
    # checkout. Se crean una vez por Plan (vía dashboard o API de cada proveedor) y se
    # pegan acá -- vacíos hasta que se configuren, ver pagos/views.py:checkout_iniciar_plan().
    mercadopago_plan_id = models.CharField(max_length=100, blank=True, help_text="ID del plan de preaprobación ya creado del lado de MercadoPago (preapproval_plan_id).")
    paypal_plan_id = models.CharField(max_length=100, blank=True, help_text="ID del plan de facturación ya creado del lado de PayPal (Billing Plan id).")

    class Meta:
        verbose_name = "Plan"
        verbose_name_plural = "Planes"

    def __str__(self):
        return self.nombre


class Suscripcion(models.Model):
    ESTADO_CHOICES = [
        ('activa', 'Activa'),
        ('pago_fallido', 'Pago fallido'),
        ('cancelada', 'Cancelada'),
        ('expirada', 'Expirada'),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='suscripciones')
    # PROTECT, no CASCADE ni SET_NULL: borrar un Plan con suscripciones vivas/históricas
    # tiene que ser un error explícito que fuerce una decisión consciente (desactivar el
    # Plan en cambio, ver Plan.activo) -- nunca un cascade silencioso que borre historial
    # de facturación, ni un SET_NULL que dejaría tiene_acceso() sin poder resolver
    # plan.incluye_todos_los_cursos/plan.features para esa fila.
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='suscripciones')
    proveedor = models.CharField(max_length=20, choices=PROVEEDOR_CHOICES)
    id_externo = models.CharField(max_length=100, unique=True, help_text="ID de la suscripción en el proveedor (MercadoPago preapproval id / PayPal subscription id). Estable a través de renovaciones.")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='activa', help_text="Solo para diagnóstico/admin -- NUNCA usar este campo para decidir acceso. El acceso real se calcula SIEMPRE comparando fecha_fin_periodo_actual contra ahora (ver pagos/access.py:tiene_acceso()). Cancelar no corta el acceso al instante; un pago fallido nunca lo otorga, pero tampoco recorta un período ya pagado.")
    moneda = models.CharField(max_length=3, choices=MONEDA_CHOICES)
    precio_pagado = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_inicio = models.DateTimeField()
    fecha_fin_periodo_actual = models.DateTimeField(help_text="Fin del período ya pagado. ÚNICO campo que tiene_acceso() consulta para decidir acceso por suscripción -- se actualiza en cada renovación exitosa, NO se toca al cancelar (la cancelación solo deja de renovar a futuro) ni al fallar un pago (un pago fallido no recorta un período ya pagado, solo impide que se extienda).")
    cancelada_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Suscripción"
        verbose_name_plural = "Suscripciones"
        indexes = [
            models.Index(fields=['usuario', 'fecha_fin_periodo_actual']),
        ]

    def __str__(self):
        return f"{self.usuario.username} - {self.plan.nombre} ({self.estado})"


class CompraIndividual(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='compras_individuales')
    # La clave REAL de acceso -- ver pagos/access.py:tiene_acceso(). NO curso_id: Curso
    # (en trainer/models.py) tiene una fila separada por idioma que comparte `codigo`
    # (unique_together = ('codigo', 'idioma')), así que comprar un curso tiene que
    # desbloquearlo sin importar en qué idioma esté navegando el usuario en ese momento
    # -- una compra atada a una fila puntual de Curso cobraría dos veces por el mismo
    # contenido solo por el idioma activo.
    curso_codigo = models.CharField(max_length=140, help_text="Copia de Curso.codigo (mismo max_length que ese campo) al momento de la compra. Es la clave real de acceso -- ver curso_codigo vs curso más abajo.")
    # Conveniencia de admin únicamente (mostrar nombre/idioma reales en el changelist) --
    # NUNCA leído por pagos/access.py, que solo usa curso_codigo. SET_NULL + nullable:
    # si esa fila puntual de Curso se borra después, la compra y el acceso tienen que
    # sobrevivir igual (dependen de curso_codigo, no de esta FK).
    curso = models.ForeignKey('trainer.Curso', on_delete=models.SET_NULL, null=True, blank=True, related_name='compras_individuales', help_text="Referencia de conveniencia a la fila Curso específica elegida en el checkout, solo para mostrar nombre/idioma en el admin. La fuente de verdad de acceso es curso_codigo, no este campo.")
    proveedor = models.CharField(max_length=20, choices=PROVEEDOR_CHOICES)
    id_externo = models.CharField(max_length=100, unique=True, help_text="ID del pago/orden en el proveedor (MercadoPago payment id / PayPal order id).")
    moneda = models.CharField(max_length=3, choices=MONEDA_CHOICES)
    precio_pagado = models.DecimalField(max_digits=10, decimal_places=2)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Compra Individual"
        verbose_name_plural = "Compras Individuales"
        unique_together = ('usuario', 'curso_codigo')

    def __str__(self):
        return f"{self.usuario.username} - {self.curso_codigo}"


class EventoPago(models.Model):
    # Nullable + SET_NULL: un webhook puede llegar antes de poder resolver a qué User
    # local corresponde (id_externo todavía no vinculado a ninguna Suscripcion/
    # CompraIndividual, o el lookup falla) -- payload_raw completo alcanza para
    # diagnosticar manualmente igual, perder la fila entera sería peor que dejarla sin
    # usuario resuelto.
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='eventos_pago')
    proveedor = models.CharField(max_length=20, choices=PROVEEDOR_CHOICES)
    tipo_evento = models.CharField(max_length=80, help_text="Tipo de evento tal cual lo manda el proveedor (ej. 'payment.updated', 'BILLING.SUBSCRIPTION.CANCELLED'). CharField libre, sin choices -- cada proveedor tiene su propio catálogo y puede sumar tipos nuevos sin necesitar una migración acá.")
    id_evento_externo = models.CharField(max_length=150, blank=True, db_index=True, help_text="ID del evento/notificación del proveedor, usado para el chequeo de idempotencia antes de aplicar efectos (ver pagos/webhooks.py). NO es unique a propósito -- cada entrega recibida se loguea, incluso reintentos/duplicados del proveedor; lo idempotente es el EFECTO sobre Suscripcion/CompraIndividual, no este log.")
    payload_raw = models.JSONField(help_text="Body crudo del webhook, completo, sin transformar -- para reconstruir manualmente qué pasó ante cualquier duda, sin depender del dashboard del proveedor.")
    procesado_ok = models.BooleanField(default=False)
    # Distingue "esto es tráfico esperado" (duplicado) de "esto necesita que alguien lo
    # mire" (firma inválida, proveedor no configurado) mediante un campo real -- no
    # parseando `detalle` como texto libre. Dispara mail_admins() en el momento (ver
    # pagos/webhooks.py) y es el filtro exacto del management command de reporte
    # (pagos/management/commands/reporte_eventos_fallidos.py). NUNCA True para
    # duplicados: son tráfico esperado del proveedor reintentando, alertar ahí sería
    # puro ruido.
    requiere_atencion = models.BooleanField(default=False, help_text="True solo para firma inválida o proveedor no configurado -- NUNCA para duplicados (tráfico esperado). Dispara mail_admins() en el momento (ver pagos/webhooks.py) y es lo que filtra el management command reporte_eventos_fallidos.")
    detalle = models.CharField(max_length=255, blank=True, help_text="Texto corto de diagnóstico: motivo de rechazo de firma, 'duplicado, no-op', qué campo se actualizó, etc.")
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evento de Pago"
        verbose_name_plural = "Eventos de Pago"
        indexes = [
            models.Index(fields=['proveedor', 'tipo_evento', 'id_evento_externo']),
            models.Index(fields=['requiere_atencion', 'creado_en']),
        ]

    def __str__(self):
        return f"{self.proveedor} - {self.tipo_evento} ({'OK' if self.procesado_ok else 'sin procesar'})"
