from django.contrib import admin
from .models import Feature, Plan, Suscripcion, CompraIndividual, EventoPago


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo')
    search_fields = ('nombre', 'codigo')


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'codigo', 'precio_ars', 'precio_usd', 'incluye_todos_los_cursos', 'activo')
    list_editable = ('activo',)
    filter_horizontal = ('features',)


@admin.register(Suscripcion)
class SuscripcionAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'plan', 'fecha_fin_periodo_actual', 'proveedor', 'id_externo', 'estado', 'cancelada_en')
    list_filter = ('proveedor', 'estado', 'plan')
    search_fields = ('usuario__username', 'usuario__email', 'id_externo')
    readonly_fields = ('creado_en', 'actualizado_en')


@admin.register(CompraIndividual)
class CompraIndividualAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'curso_codigo', 'curso', 'proveedor', 'id_externo', 'fecha')
    list_filter = ('proveedor',)
    search_fields = ('usuario__username', 'usuario__email', 'curso_codigo', 'id_externo')


@admin.register(EventoPago)
class EventoPagoAdmin(admin.ModelAdmin):
    # Log de auditoría -- nunca editable a mano, ver readonly_fields abajo. Si hace falta
    # corregir el estado real de un usuario, se edita Suscripcion/CompraIndividual
    # directamente (una acción visible y deliberada), nunca reescribiendo este historial.
    list_display = ('creado_en', 'proveedor', 'tipo_evento', 'usuario', 'id_evento_externo', 'procesado_ok', 'requiere_atencion')
    list_filter = ('proveedor', 'procesado_ok', 'requiere_atencion', 'tipo_evento')
    search_fields = ('usuario__username', 'usuario__email', 'id_evento_externo')
    readonly_fields = ('usuario', 'proveedor', 'tipo_evento', 'id_evento_externo', 'payload_raw', 'procesado_ok', 'requiere_atencion', 'detalle', 'creado_en')
