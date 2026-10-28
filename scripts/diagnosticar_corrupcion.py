"""
Diagnóstico de SOLO LECTURA sobre ScoreAnalysis -- nunca modifica nada (sin
.save()/.update()), no llama a la API de Claude ni descifra la partitura
(nunca toca score_file, no necesita SCORE_FILE_ENCRYPTION_KEY, solo
DJANGO_SECRET_KEY para que Django arranque, mismo criterio que
scripts/auditar_analisis.py).

Dos cosas a la vez:

1. Busca filas donde 'bloques' o 'resumen_por_instrumento' (dentro de
   analysis_data) terminaron siendo un STRING en vez de una lista (el
   incidente real de bloques/resumen_por_instrumento colgando el PDF/las
   auditorías) y las diagnostica en detalle:
     - ¿El string es JSON válido en sí mismo (doblemente serializado --
       recuperable con un json.loads de más) o es JSON a medio cerrar
       (llaves/corchetes/comillas sin balancear -- apunta a truncamiento
       por max_tokens)?
     - Tamaño de la obra (puntaje_obra, instrumentos, compases -- estos
       tres se calculan con music21 ANTES de llamar a Claude, así que
       siguen siendo confiables aunque la RESPUESTA de Claude se haya
       corrompido).
     - 'truncado' (guardado en analysis_data, no depende del string
       corrupto) es el proxy exacto de stop_reason: True = 'max_tokens',
       False = 'end_turn'. El stop_reason como string literal NUNCA se
       persiste (solo se loggea en el momento), pero este campo booleano
       sí, y alcanza para responder la pregunta.

2. Lista TODAS las filas con tiempo_generacion_segundos (no solo las
   corruptas), con puntaje_obra/instrumentos/compases/tiempo/tokens, para
   poder comparar el tamaño de la obra corrupta contra las que sí salieron
   bien -- mismo dato que hace falta para el punto 1 del plan de tamaño de
   obra / max_tokens.

Uso:
    python scripts/diagnosticar_corrupcion.py          # todas las filas
    python scripts/diagnosticar_corrupcion.py 26       # solo esa, diagnóstico detallado si corresponde

IMPORTANTE -- no usar "python manage.py shell < scripts/diagnosticar_corrupcion.py"
(mismo problema ya documentado en auditar_analisis.py: la consola interactiva
corta las funciones en cada línea en blanco interna). Usar en cambio:
    python scripts/diagnosticar_corrupcion.py
    ANALYSIS_ID=26 python manage.py shell -c "exec(open('scripts/diagnosticar_corrupcion.py').read())"
"""
import json
import os
import sys

if '__file__' in globals():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from trainer.models import ScoreAnalysis


def _id_filtro():
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        return int(sys.argv[1])
    valor = os.environ.get('ANALYSIS_ID')
    return int(valor) if valor and valor.isdigit() else None


def _stop_reason_real(data):
    truncado = data.get('truncado')
    if truncado is True:
        return 'max_tokens'
    if truncado is False:
        return 'end_turn'
    return None  # análisis de una versión anterior a este campo


def _diagnosticar_string_corrupto(valor, nombre_campo):
    print(f"    --- {nombre_campo}: diagnóstico de corrupción ---")
    print(f"    longitud: {len(valor)} caracteres")
    print(f"    primeros 200: {valor[:200]!r}")
    print(f"    últimos 200:  {valor[-200:]!r}")

    try:
        parseado = json.loads(valor)
        print(f"    json.loads(valor) TUVO ÉXITO -- es JSON doblemente serializado "
              f"(tipo real adentro: {type(parseado).__name__}). RECUPERABLE con un json.loads de más, "
              f"no es truncamiento.")
        return
    except json.JSONDecodeError as e:
        print(f"    json.loads(valor) FALLÓ: {e}")

    abre_llave, cierra_llave = valor.count('{'), valor.count('}')
    abre_corchete, cierra_corchete = valor.count('['), valor.count(']')
    comillas = valor.count('"')
    print(f"    balance llaves {{}}: {abre_llave} abren / {cierra_llave} cierran (diferencia {abre_llave - cierra_llave})")
    print(f"    balance corchetes []: {abre_corchete} abren / {cierra_corchete} cierran (diferencia {abre_corchete - cierra_corchete})")
    print(f"    comillas dobles totales: {comillas} ({'IMPAR -- quedó una sin cerrar' if comillas % 2 else 'par'})")
    if abre_llave != cierra_llave or abre_corchete != cierra_corchete or comillas % 2 != 0:
        print("    => Estructura JSON SIN CERRAR -- consistente con TRUNCAMIENTO (stop_reason=max_tokens "
              "o corte a mitad de stream).")
    else:
        print("    => Estructura balanceada pero igual no es JSON válido -- corrupción de otro tipo, "
              "no un truncamiento simple (ej. error de formato del modelo, no relacionado al largo).")


def main():
    id_filtro = _id_filtro()
    qs = ScoreAnalysis.objects.filter(tiempo_generacion_segundos__isnull=False).order_by('created_at')
    if id_filtro is not None:
        qs = qs.filter(id=id_filtro)

    filas = list(qs)
    if not filas:
        print(f"No se encontraron análisis{f' con id={id_filtro}' if id_filtro else ''}.")
        return

    print("=" * 100)
    print("TABLA COMPLETA (para comparar tamaño de obra -- punto 1 del plan de tamaño/max_tokens)")
    print("=" * 100)
    encabezado = f"{'id':>4} {'nombre':<28} {'puntaje':>8} {'instr':>6} {'compases':>9} {'tiempo_s':>9} {'tok_salida':>11} {'stop_reason':>12} {'corrupto':>9}"
    print(encabezado)
    corruptas = []
    for a in filas:
        data = a.analysis_data or {}
        instrumentos = len(data.get('estadisticas_por_instrumento') or {})
        compases = len(data.get('densidad_por_compas') or [])
        bloques = data.get('bloques')
        resumen = data.get('resumen_por_instrumento')
        es_corrupta = isinstance(bloques, str) or isinstance(resumen, str)
        if es_corrupta:
            corruptas.append(a)
        stop_reason = _stop_reason_real(data) or '?'
        print(
            f"{a.id:>4} {a.name[:28]:<28} {str(a.puntaje_obra or '-'):>8} {instrumentos:>6} {compases:>9} "
            f"{a.tiempo_generacion_segundos:>9.1f} {str(a.output_tokens or '-'):>11} {stop_reason:>12} "
            f"{'SI' if es_corrupta else '-':>9}"
        )

    if not corruptas:
        print()
        print("Ninguna fila en este rango tiene 'bloques'/'resumen_por_instrumento' corrupto (como string).")
        return

    for a in corruptas:
        data = a.analysis_data or {}
        print()
        print("=" * 100)
        print(f"DIAGNÓSTICO DETALLADO -- ScoreAnalysis id={a.id}  nombre={a.name!r}  creado={a.created_at}")
        print("=" * 100)
        print(f"  puntaje_obra: {a.puntaje_obra}")
        print(f"  instrumentos (estadisticas_por_instrumento): {len(data.get('estadisticas_por_instrumento') or {})}")
        print(f"  compases (densidad_por_compas): {len(data.get('densidad_por_compas') or [])}")
        print(f"  tiempo_generacion_segundos: {a.tiempo_generacion_segundos}")
        print(f"  tokens_por_segundo: {a.tokens_por_segundo}")
        print(f"  input_tokens: {a.input_tokens}  output_tokens: {a.output_tokens}")
        stop_reason = _stop_reason_real(data)
        if stop_reason == 'max_tokens':
            print("  stop_reason real: 'max_tokens' (vía 'truncado'=True, guardado en su momento) "
                  "=> la respuesta SE CORTÓ por el techo de longitud.")
        elif stop_reason == 'end_turn':
            print("  stop_reason real: 'end_turn' (vía 'truncado'=False) "
                  "=> la respuesta NO se cortó por longitud -- la corrupción, si la hay, vino de otra causa.")
        else:
            print("  stop_reason real: desconocido (análisis de una versión sin el campo 'truncado')")
        print()
        if isinstance(data.get('bloques'), str):
            _diagnosticar_string_corrupto(data['bloques'], 'bloques')
        if isinstance(data.get('resumen_por_instrumento'), str):
            _diagnosticar_string_corrupto(data['resumen_por_instrumento'], 'resumen_por_instrumento')


main()
