"""
Auditoría de SOLO LECTURA sobre un ScoreAnalysis ya guardado en la base --
aplica, con el código ACTUAL de trainer/views.py y trainer/metricas_ejecucion.py,
las 3 auditorías de la capa de alertas_ejecucion/bloques sobre el informe que
YA se generó (analysis.analysis_data), sin llamar a la API de Claude ni volver
a parsear/descifrar la partitura (nunca toca analysis.score_file):

  - _auditar_citas_ejecucion
  - detectar_alertas_criticas_no_comentadas
  - _auditar_identificadores_con_guion_bajo

Ninguna de las 3 modifica nada (son solo logging) -- este script tampoco hace
ningún .save()/.update().

Variables de entorno necesarias: SOLO DJANGO_SECRET_KEY (Django no arranca sin
ella). NO hace falta SCORE_FILE_ENCRYPTION_KEY -- ese código vive en
trainer/storage.py y solo se ejecuta cuando algo llama a
analysis.score_file.open()/.save(), y este script nunca toca ese campo, solo
lee analysis.analysis_data (un JSONField plano) y los demás campos numéricos
del modelo.

Uso (dos formas equivalentes):

    python scripts/auditar_analisis.py 26
    ANALYSIS_ID=26 python manage.py shell -c "exec(open('scripts/auditar_analisis.py').read())"

Sin argumento ni ANALYSIS_ID, audita el id=26 (el último usado para
"Flauta exagerada" en esta conversación) por default.

IMPORTANTE -- NO uses "python manage.py shell < scripts/auditar_analisis.py"
(redirección de entrada): la consola interactiva estándar de Python trata
cada línea en blanco como fin de bloque, y este archivo tiene líneas en
blanco DENTRO de la función main() (solo para separar visualmente) -- eso
rompe la ejecución con "IndentationError: unexpected indent" a mitad de
camino (confirmado empíricamente, no es una suposición). "exec(open(...).read())"
compila el archivo entero de una sola vez, como lo haría "python archivo.py",
y no tiene ese problema.
"""
import logging
import os
import sys

# Al correr como archivo suelto ("python scripts/auditar_analisis.py"), Python
# pone el directorio del SCRIPT (scripts/) en sys.path, no la raíz del
# proyecto -- "import config"/"import trainer" fallarían sin esto. Al correr
# pegado a "manage.py shell <", __file__ no existe (el código llega por stdin
# a la consola interactiva) y manage.py ya se encargó de sys.path -- por eso
# el ajuste queda condicionado a que __file__ exista.
if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from trainer.models import ScoreAnalysis
from trainer.views import (
    _auditar_citas_ejecucion,
    detectar_alertas_criticas_no_comentadas,
    _auditar_identificadores_con_guion_bajo,
)
from trainer.metricas_ejecucion import compactar_alertas_para_prompt


class _CapturadorDeWarnings(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lineas = []

    def emit(self, record):
        self.lineas.append(self.format(record))


def _id_analisis():
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        return int(sys.argv[1])
    return int(os.environ.get('ANALYSIS_ID', 26))


def _imprimir_resultado(titulo, lineas):
    print(f"--- {titulo} ---")
    if lineas:
        for linea in lineas:
            print(f"  [WARNING] {linea}")
    else:
        print("  sin hallazgos")
    print()


def main():
    id_analisis = _id_analisis()
    try:
        analysis = ScoreAnalysis.objects.get(id=id_analisis)
    except ScoreAnalysis.DoesNotExist:
        print(f"No existe ScoreAnalysis id={id_analisis}.")
        return

    data = analysis.analysis_data or {}

    print("=" * 70)
    print(f"ScoreAnalysis id={analysis.id}  nombre={analysis.name!r}  creado={analysis.created_at}")
    print("=" * 70)
    print("DATOS DEL ANALISIS")
    print(f"  tiempo_generacion_segundos: {analysis.tiempo_generacion_segundos}")
    print(f"  tokens_por_segundo:         {analysis.tokens_por_segundo}")
    print(f"  input_tokens:               {analysis.input_tokens}")
    print(f"  output_tokens:              {analysis.output_tokens}")
    truncado = data.get('truncado')
    if truncado is True:
        stop_reason = 'max_tokens'
    elif truncado is False:
        stop_reason = 'end_turn'
    else:
        stop_reason = 'desconocido (análisis viejo, sin el campo "truncado")'
    print(f"  stop_reason (vía 'truncado'): {stop_reason}")
    print(f"  creditos_cobrados:          {analysis.creditos_cobrados}")
    print(f"  puntaje_obra:               {analysis.puntaje_obra}")
    print()

    bloques = data.get('bloques')
    alertas_ejecucion = data.get('alertas_ejecucion') or {}
    alertas_compactas = compactar_alertas_para_prompt(alertas_ejecucion)

    logger_trainer_views = logging.getLogger('trainer.views')
    captador = _CapturadorDeWarnings()
    logger_trainer_views.addHandler(captador)
    logger_trainer_views.setLevel(logging.DEBUG)
    nivel_previo = logger_trainer_views.propagate
    logger_trainer_views.propagate = False  # no duplicar en la consola, solo capturar

    try:
        captador.lineas = []
        _auditar_citas_ejecucion(bloques, alertas_compactas)
        _imprimir_resultado('_auditar_citas_ejecucion', captador.lineas)

        no_comentadas = detectar_alertas_criticas_no_comentadas(bloques, alertas_compactas)
        print("--- detectar_alertas_criticas_no_comentadas ---")
        if no_comentadas:
            for alerta in no_comentadas:
                print(f"  [SIN COMENTAR] {alerta}")
        else:
            print("  sin hallazgos")
        print()

        captador.lineas = []
        _auditar_identificadores_con_guion_bajo(data)
        _imprimir_resultado('_auditar_identificadores_con_guion_bajo', captador.lineas)
    finally:
        logger_trainer_views.removeHandler(captador)
        logger_trainer_views.propagate = nivel_previo

    print("=" * 70)


main()
