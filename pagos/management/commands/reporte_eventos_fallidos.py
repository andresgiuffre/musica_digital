from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from pagos.models import EventoPago


class Command(BaseCommand):
    """
    Respaldo/auditoría manual para EventoPago(requiere_atencion=True) -- complementa la
    alerta por mail_admins() que ya se dispara en el momento (ver
    pagos/webhooks.py:_alertar_fallo_webhook()), útil si el email nunca llegó a
    configurarse (funciona standalone, cero infraestructura nueva) o para revisar el
    historial completo, no solo el instante en que algo falló.

    Pensado para correr a mano o vía una tarea programada de PythonAnywhere (ej. diaria).
    Sale con código de salida distinto de cero si encontró algo -- así una tarea
    programada lo puede usar como señal de "algo anda mal", y correrlo a mano da un
    pass/fail claro tipo Unix.
    """
    help = "Reporta EventoPago(requiere_atencion=True) de las últimas N horas (default 24)."

    def add_arguments(self, parser):
        parser.add_argument('--horas', type=int, default=24, help="Ventana de tiempo hacia atrás a revisar (default 24).")

    def handle(self, *args, **options):
        horas = options['horas']
        desde = timezone.now() - timedelta(hours=horas)
        eventos = EventoPago.objects.filter(requiere_atencion=True, creado_en__gte=desde).order_by('creado_en')

        if not eventos.exists():
            self.stdout.write(self.style.SUCCESS(f"Sin eventos que requieran atención en las últimas {horas}hs."))
            return

        self.stdout.write(self.style.ERROR(f"{eventos.count()} evento(s) que requieren atención en las últimas {horas}hs:"))
        for e in eventos:
            self.stdout.write(f"  [{e.creado_en:%Y-%m-%d %H:%M:%S}] {e.proveedor} / {e.tipo_evento} / id_evento_externo={e.id_evento_externo!r} -- {e.detalle}")

        raise SystemExit(1)
