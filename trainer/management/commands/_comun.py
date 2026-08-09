"""Helpers compartidos entre los management commands de curación LOCAL
(triturar_piezas, reducir_a_piano). Empieza con "_" a propósito: Django
ignora los módulos que arrancan con guión bajo al listar comandos
(confirmado contra find_commands() de Django), así que esto nunca aparece
como un "manage.py _comun" fantasma.

No importa nada de trainer.models/views/urls -- estos comandos son
autocontenidos respecto del sitio, solo comparten lógica entre sí.
"""
import music21

EXTENSIONES_VALIDAS = {'.musicxml', '.xml', '.mxl'}


def identificar_voces(partes):
    """Voz superior = la Part de pitch promedio más alto (la línea "cantable").
    Devuelve (voz_superior, resto_ascendente) -- resto_ascendente es la lista
    de las demás partes ordenadas de más grave a más aguda (resto_ascendente[0]
    es "el bajo" para quien solo necesite una voz grave; la lista completa es
    "el acompañamiento" para quien necesite todo). Si hay una sola parte,
    resto_ascendente es [].
    """
    def pitch_promedio(parte):
        alturas = [p.ps for n in parte.recurse().notes for p in n.pitches]
        return sum(alturas) / len(alturas) if alturas else None

    candidatas = [(parte, pitch_promedio(parte)) for parte in partes]
    candidatas = [(p, prom) for p, prom in candidatas if prom is not None]
    if not candidatas:
        raise ValueError("ninguna parte tiene notas")

    candidatas.sort(key=lambda x: x[1])
    voz_superior = candidatas[-1][0]
    resto_ascendente = [p for p, _ in candidatas[:-1]]
    return voz_superior, resto_ascendente


class EscritorLog:
    """Escribe cada mensaje a stdout (con estilo, vía el Command) y a un
    log.txt persistente en la carpeta de salida. Evita depender de
    settings.LOGGING para una herramienta que no toca el sitio.
    """

    def __init__(self, command, carpeta_salida):
        self._command = command
        self._fh = open(carpeta_salida / 'log.txt', 'w', encoding='utf-8')

    def log(self, nivel, mensaje):
        linea = f"[{nivel.upper()}] {mensaje}"
        self._fh.write(linea + '\n')
        self._fh.flush()
        estilo = {
            'error': self._command.style.ERROR,
            'warning': self._command.style.WARNING,
        }.get(nivel)
        self._command.stdout.write(estilo(linea) if estilo else linea)

    def cerrar(self):
        self._fh.close()


def normalizar_a_score(objeto_music21):
    """converter.parse() puede devolver un Part suelto para piezas
    monofónicas sin envoltura explícita de Score -- lo normaliza siempre a
    Score para que el resto del pipeline pueda asumir .parts consistentemente.
    """
    if isinstance(objeto_music21, music21.stream.Part):
        contenedor = music21.stream.Score()
        contenedor.insert(0, objeto_music21)
        return contenedor
    return objeto_music21
