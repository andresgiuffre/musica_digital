"""
Postproceso del HTML del PDF del curso (ver curso_exportar_pdf en views.py)
antes de pasarlo a xhtml2pdf -- arregla problemas de maquetación que ESTE
MOTOR no puede resolver con CSS declarativo, confirmado leyendo
xhtml2pdf/parser.py y probando contra la librería real (no asumido):

- `page-break-inside: avoid` está completamente sin implementar en
  xhtml2pdf 0.2.17 -- no aparece en ningún lado del paquete, y un bloque
  marcado así se parte igual entre dos páginas (confirmado con un render
  de prueba). La regla ya existente en curso_pdf.html (.tema-pdf-bloque)
  nunca hizo nada.
- `max-width`/`max-height` en <img> tampoco se leen nunca -- sólo
  width/height explícitos (confirmado leyendo xhtml2pdf/tags.py,
  pisaTagIMG.start). El `max-width:100%` que ya usa este mismo template
  en otras imágenes no hace nada en este motor.
- En cambio `-pdf-keep-with-next` SÍ es una propiedad real que xhtml2pdf
  reconoce (parser.py la mapea a ParagraphStyle(keepWithNext=...), un
  feature nativo de reportlab) -- probado que encadena correctamente a
  través de <img>, <table> y <li>, no sólo párrafos. Es la base de casi
  toda la solución a los huérfanos: la mayoría de los casos (títulos
  reales h1-h6, .grado-titulo, .tema-titulo, .indice-grado, el <p class="pie">
  de una partitura) se resuelven con esa propiedad directo en el <style>
  de curso_pdf.html, sin pasar por acá. Este módulo cubre únicamente lo
  que CSS no puede expresar: decidir en Python qué párrafo suelto
  funciona como etiqueta de un gráfico, y qué <img> consecutivas
  pertenecen al mismo grupo.

Usa lxml.html (ya es una dependencia instalada -- de hecho xhtml2pdf la
usa internamente) y Pillow para leer el tamaño real de las imágenes --
Pillow YA es una dependencia transitiva obligatoria de xhtml2pdf (la
necesita para decodificar cualquier <img>, confirmado con
`pip show xhtml2pdf`), así que usarla acá no suma peso nuevo al entorno,
sólo la vuelve una dependencia directa y declarada en vez de un
transitivo no documentado -- ver requirements.txt.
"""
import re

from lxml import html as lxml_html
from PIL import Image

_SRC_CON_ESPACIOS_CODIFICADOS = re.compile(r'src="([^"]*)"')


# --- Problema 1 (parcial) / Problema 2: etiquetas cortas antes de un
# gráfico, y grupos de <img> consecutivas dentro de un mismo bloque de
# partitura ---

_CLASES_BLOQUE_GRAFICO = ('bloque-imagen', 'bloque-partitura', 'bloque-partitura-dividida')
_FIN_DE_ORACION = ('.', '!', '?', ':', ';', ',')
_LARGO_MAXIMO_ETIQUETA = 120


def _es_etiqueta_corta(texto):
    """
    Heurística para "esto es una etiqueta/caption, no una oración de
    prosa" (ver el pedido del usuario, punto 1: "todo párrafo corto sin
    punto final que funcione como etiqueta de un gráfico"). Corto y sin
    puntuación de cierre. A propósito PERMISIVA: un falso positivo sólo
    le agrega -pdf-keep-with-next a un párrafo normal, que en el peor
    caso mueve levemente un corte de página -- nunca rompe nada visual.
    """
    texto = (texto or '').strip()
    if not texto or len(texto) > _LARGO_MAXIMO_ETIQUETA:
        return False
    return not texto.endswith(_FIN_DE_ORACION)


def _agregar_estilo(el, declaracion):
    """Suma una declaración CSS al atributo style de un elemento lxml sin pisar lo que ya tuviera."""
    actual = el.get('style', '') or ''
    if actual and not actual.rstrip().endswith(';'):
        actual += ';'
    el.set('style', f'{actual}{declaracion};')


def _encadenar_etiquetas_con_graficos(arbol):
    """
    Cubre el caso que -pdf-keep-with-next no puede resolver solo con CSS:
    un párrafo de cierre de un bloque de TEXTO que en realidad funciona
    como título de un gráfico que viene en el BloqueContenido SIGUIENTE
    (IMAGEN, EJEMPLO_PARTITURA o PARTITURA_DIVIDIDA -- bloques hermanos
    dentro de .tema-pdf-bloque, ver curso_pdf.html). Ejemplos reales:
    "El Pentagrama", "Compasillo", "Semitonos diatónicos".

    Los títulos reales (h1-h6 dentro de .contenido-md, .grado-titulo,
    .tema-titulo, .indice-grado) y el <p class="pie"> que antecede a una
    partitura ya se resuelven con CSS puro en curso_pdf.html -- esto es
    sólo para el <p> suelto que no es ninguna de esas cosas.
    """
    for contenido in arbol.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' contenido-md ')]"):
        hijos = [h for h in contenido if isinstance(h.tag, str)]
        if not hijos or hijos[-1].tag != 'p':
            continue
        ultimo = hijos[-1]

        siguiente = contenido.getnext()
        if siguiente is None:
            continue
        clases_siguiente = (siguiente.get('class') or '').split()
        if not any(c in _CLASES_BLOQUE_GRAFICO for c in clases_siguiente):
            continue

        if not _es_etiqueta_corta(ultimo.text_content()):
            continue
        _agregar_estilo(ultimo, '-pdf-keep-with-next: true')


def _encadenar_imagenes_de_partitura(arbol):
    """
    Dentro de un mismo bloque EJEMPLO_PARTITURA con varias imágenes
    apiladas (un archivo con cortes de sección genera una imagen por
    sección, ver construirSeccionesXml en tema_detail.html -- ej. las
    tres variantes Antigua/Armónica/Melódica de una escala menor),
    encadena -pdf-keep-with-next entre todas menos la última para que el
    grupo completo se mueva junto si no entra en lo que queda de la
    página actual, en vez de partirse en cualquier punto intermedio.
    """
    for bloque in arbol.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' bloque-partitura ')]"):
        imagenes = [h for h in bloque if h.tag == 'img']
        for img in imagenes[:-1]:
            _agregar_estilo(img, '-pdf-keep-with-next: true')


# --- Problema 3a: tabla que se parte entre páginas sin repetir el
# encabezado, y celdas de más/de menos por fila mal tipeadas a mano ---

def _normalizar_y_repetir_encabezado_tablas(arbol):
    """
    xhtml2pdf no tiene <thead> real -- confirmado que SÍ soporta repetir
    filas de encabezado vía el atributo HTML propio <table repeat="N">
    (probado empíricamente: repite las primeras N filas en cada página
    donde la tabla se parte; ver xhtml2pdf/tables.py, tdata.repeat). Se
    aplica a TODA tabla de .contenido-md que tenga <thead>, no sólo a la
    que reportó el usuario -- "revisá que ninguna otra tabla tenga el
    mismo problema".

    De paso normaliza la cantidad de <td>/<th> por fila: si una fila del
    Markdown original tiene menos celdas que el máximo de la tabla (un
    typo humano al escribir la tabla), reportlab arma mal la grilla de
    columnas -- se completa con celdas vacías en vez de dejar que se
    desalinee.
    """
    for tabla in arbol.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " contenido-md ")]//table'):
        filas = tabla.xpath('.//tr')
        if not filas:
            continue

        n_columnas = max(len(fila.xpath('./td | ./th')) for fila in filas)
        for fila in filas:
            celdas = fila.xpath('./td | ./th')
            for _ in range(n_columnas - len(celdas)):
                fila.append(lxml_html.Element('td'))

        encabezado = tabla.xpath('./thead/tr')
        if encabezado:
            tabla.set('repeat', str(len(encabezado)))


# --- Problema 4: imágenes grandes (bloques IMAGEN) saltan enteras a la
# página siguiente dejando un hueco grande en la anterior ---

# A4 (21cm x 29.7cm) menos los márgenes de @page en curso_pdf.html
# (2.2cm arriba/abajo, 1.8cm izq/der) = área imprimible 17.4cm x 25.3cm.
_ANCHO_MAXIMO_CM = 17.0  # mismo ancho fijo ya usado para las partituras
_ALTO_MAXIMO_CM = 20.0  # ~80% del alto imprimible (25.3cm), pedido por el usuario


def _limitar_tamano_imagenes_grandes(arbol):
    """
    Calcula un width/height explícito en cm para cada <img> de un bloque
    IMAGEN (no toca .bloque-partitura/-dividida, que ya traen su propio
    ancho fijo de 17cm vía CSS), respetando la proporción real del
    archivo y sin superar ni el ancho ni el ~80% del alto imprimible --
    lo que exija achicar más, manda. Sin esto, una imagen sin
    width/height explícito se dibuja a su tamaño de píxel nativo (porque
    max-width/max-height no hacen nada en este motor, ver el comentario
    grande arriba), que para estos gráficos de infografía puede superar
    el área imprimible completa y forzar el salto de página + hueco en
    blanco que reportó el usuario.

    El src de estas <img> ya es una ruta de disco absoluta, no una URL --
    mismo patrón que bloque.imagen_pdf_path en curso_exportar_pdf (la
    vista arma el PDF en el mismo proceso, con acceso directo al
    filesystem), así que se puede abrir directo con Pillow. Si el archivo
    no se puede abrir (ej. un SVG -- IMAGEN acepta PNG/JPG/SVG, ver
    BloqueContenido.imagen en models.py) se deja la imagen sin tocar,
    igual que el comportamiento de hoy.
    """
    for img in arbol.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' bloque-imagen ')]/img"):
        ruta = img.get('src')
        if not ruta:
            continue
        try:
            with Image.open(ruta) as im:
                ancho_px, alto_px = im.size
        except Exception:
            continue
        if not ancho_px or not alto_px:
            continue

        ancho_cm = _ANCHO_MAXIMO_CM
        alto_cm = ancho_cm * alto_px / ancho_px
        if alto_cm > _ALTO_MAXIMO_CM:
            alto_cm = _ALTO_MAXIMO_CM
            ancho_cm = alto_cm * ancho_px / alto_px

        _agregar_estilo(img, f'width: {ancho_cm:.2f}cm; height: {alto_cm:.2f}cm')


def corregir_maquetacion_pdf(html_string):
    """
    Punto de entrada único -- se llama sobre el HTML ya renderizado por
    curso_pdf.html, justo antes de pisa.CreatePDF (ver curso_exportar_pdf
    en views.py).
    """
    arbol = lxml_html.fromstring(html_string)
    _encadenar_etiquetas_con_graficos(arbol)
    _encadenar_imagenes_de_partitura(arbol)
    _normalizar_y_repetir_encabezado_tablas(arbol)
    _limitar_tamano_imagenes_grandes(arbol)

    # lxml_html.tostring (method='html') es el único de los dos modos de
    # serialización que NO autocierra <a name="..."></a> -- confirmado
    # con un repro real que method='xml' sí lo hace (<a name="x"/>), y
    # como <a> no es un elemento vacío en HTML5, html5lib (lo que usa
    # xhtml2pdf del lado del parser) interpreta ese autocierre como una
    # apertura sin cerrar: todo el contenido siguiente queda "adentro" de
    # ese <a> hasta el próximo </a> real, y sale pintado como link (azul
    # y subrayado) -- así se manifestó este bug la primera vez, con TODO
    # el texto después de cada ancla de Grado/Tema renderizado como link.
    #
    # El costo de usar method='html' es el inverso: percent-encodea
    # atributos tipo URI (src), así que un espacio real en una ruta de
    # disco (ej. "Musica Digital", el propio directorio de este proyecto)
    # sale como %20 y rompe la carga de la imagen -- confirmado con otro
    # repro real. Se deshace acá, sólo dentro de src="...", en vez de
    # pisar el string entero (un data: URI en base64 no tiene espacios
    # para empezar, así que nunca lo toca).
    html_final = lxml_html.tostring(arbol, encoding='unicode', doctype='<!DOCTYPE html>')
    return _SRC_CON_ESPACIOS_CODIFICADOS.sub(
        lambda m: 'src="%s"' % m.group(1).replace('%20', ' '), html_final
    )
