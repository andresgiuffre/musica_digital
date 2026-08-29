"""
Piloto aislado: transcribe un silbido grabado (.wav, sin click de metrónomo de
fondo) a una melodía musical simple, usando pYIN (librosa) para pitch tracking
monofónico y music21 para construir la partitura.

NO es la feature final -- es solo para validar si el enfoque funciona antes de
construir cualquier UI. No depende de Django ni de nada del sitio.

Uso:
    python transcribe.py --audio silbido.wav --bpm 90 --out-dir salida/

Ver README.md en esta misma carpeta para el detalle de cada paso del pipeline.
"""
import argparse
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import librosa
import music21
import numpy as np

# --- Duraciones cuantizadas permitidas (en negras/quarterLength) ---------------
# Set simple a propósito -- cuantización rítmica básica, no un quantizer
# sofisticado. Nombre legible para el reporte + valor en quarterLength para music21.
DURACIONES_PERMITIDAS = [
    ("semicorchea", 0.25),
    ("corchea", 0.5),
    ("corchea con puntillo", 0.75),
    ("negra", 1.0),
    ("negra con puntillo", 1.5),
    ("blanca", 2.0),
    ("blanca con puntillo", 3.0),
    ("redonda", 4.0),
]

# Subdivisión mínima a la que se "snapea" el tiempo de inicio de cada nota
# (semicorchea = 1/4 de negra). Mismo grid que la duración mínima permitida.
GRID_INICIO_QL = 0.25


@dataclass
class NotaDetectada:
    inicio_s: float
    fin_s: float
    midi_redondeado: int
    hz_crudo_medio: float
    confianza: float
    descartada: bool = False
    motivo_descarte: str = ""
    # Cuantizados (se completan en un paso posterior)
    inicio_ql: float = 0.0
    duracion_ql: float = 0.0
    duracion_nombre: str = ""

    @property
    def duracion_s(self) -> float:
        return self.fin_s - self.inicio_s


def cargar_audio(ruta_wav: str) -> tuple[np.ndarray, int]:
    """Carga el .wav, downmix a mono si viene en estéreo. Respeta el sample rate
    original del archivo (sr=None) -- pYIN internamente hace el resample que
    necesite, no hace falta forzarlo acá."""
    y, sr = librosa.load(ruta_wav, sr=None, mono=True)
    return y, sr


def detectar_pitch(y: np.ndarray, sr: int, fmin_hz: float, fmax_hz: float):
    """pYIN: devuelve f0 (Hz, NaN si no hay voz), voiced_flag, voiced_probs por
    frame. Acotado al rango típico de un silbido para evitar octave errors fuera
    de rango (pYIN igual puede cometer errores de octava DENTRO del rango --
    por eso el reporte final muestra el Hz crudo, para poder distinguirlos)."""
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y, fmin=fmin_hz, fmax=fmax_hz, sr=sr
    )
    tiempos = librosa.times_like(f0, sr=sr)
    return f0, voiced_flag, voiced_probs, tiempos


def detectar_onsets(y: np.ndarray, sr: int) -> np.ndarray:
    """Onsets adicionales a los cambios de pitch -- sin esto, dos notas
    consecutivas a la MISMA altura se fusionarían en una nota larga, porque la
    segmentación por cambio de pitch no ve un límite ahí.

    delta=0.1 (default de librosa es 0.07) -- confirmado empíricamente con el
    .wav sintético: con el default, el vibrato dentro de una MISMA nota sostenida
    (no repetida) generaba picos de flujo espectral suficientes para disparar
    onsets falsos a mitad de nota (partía D5 y G5 en 2-3 pedazos sin motivo).
    delta=0.1 los elimina sin perder ningún onset real, incluido el que separa
    el par de notas repetidas E5-E5."""
    return librosa.onset.onset_detect(y=y, sr=sr, units="time", delta=0.1)


def segmentar_notas(
    f0: np.ndarray,
    voiced_probs: np.ndarray,
    tiempos: np.ndarray,
    onsets_s: np.ndarray,
    hop_frames_gap_tolerado: int = 2,
) -> list[NotaDetectada]:
    """Agrupa frames voiced consecutivos en notas discretas. Un grupo se corta
    cuando: (a) cambia el MIDI redondeado, (b) hay un onset detectado en medio
    del grupo, o (c) el gap de frames unvoiced supera la tolerancia (silbar
    tiene micro-interrupciones de envolvente que no son notas nuevas)."""
    n = len(f0)
    midi_por_frame = np.full(n, np.nan)
    for i in range(n):
        if not np.isnan(f0[i]):
            midi_por_frame[i] = round(librosa.hz_to_midi(f0[i]))

    onsets_restantes = sorted(onsets_s.tolist())

    def hay_onset_en_rango(t_ini: float, t_fin: float) -> bool:
        # Inclusive en t_fin a propósito: onset_detect() y pyin() comparten el
        # mismo hop_length por default, así que un onset real cae casi siempre
        # EXACTO sobre el timestamp de un frame (confirmado empíricamente con el
        # .wav sintético: onset == tiempos[idx] al dividir la nota repetida) --
        # con desigualdad estricta en ambos lados ese caso nunca se detectaba.
        while onsets_restantes and onsets_restantes[0] < t_ini:
            onsets_restantes.pop(0)
        return bool(onsets_restantes) and t_ini < onsets_restantes[0] <= t_fin

    notas: list[NotaDetectada] = []
    i = 0
    while i < n:
        if np.isnan(midi_por_frame[i]):
            i += 1
            continue

        midi_actual = midi_por_frame[i]
        inicio_idx = i
        gap_frames = 0
        j = i + 1
        while j < n:
            if np.isnan(midi_por_frame[j]):
                gap_frames += 1
                if gap_frames > hop_frames_gap_tolerado:
                    break
                j += 1
                continue
            gap_frames = 0
            if midi_por_frame[j] != midi_actual:
                break
            # Cortar si hay un onset estrictamente dentro de este frame nuevo
            # (no solo al final del grupo -- se revisa por frame para no
            # fusionar dos notas repetidas que un onset separa a mitad del run).
            if hay_onset_en_rango(tiempos[j - 1], tiempos[j]):
                break
            j += 1

        fin_idx = j - 1
        # Recortar el gap final unvoiced que pudo haber quedado colgado
        while fin_idx > inicio_idx and np.isnan(midi_por_frame[fin_idx]):
            fin_idx -= 1

        frames_voiced = [
            k for k in range(inicio_idx, fin_idx + 1) if not np.isnan(midi_por_frame[k])
        ]
        if frames_voiced:
            hz_crudo_medio = float(
                np.nanmean([f0[k] for k in frames_voiced])
            )
            confianza = float(np.mean([voiced_probs[k] for k in frames_voiced]))
            notas.append(
                NotaDetectada(
                    inicio_s=float(tiempos[inicio_idx]),
                    fin_s=float(tiempos[fin_idx]) + (tiempos[1] - tiempos[0] if n > 1 else 0.0),
                    midi_redondeado=int(midi_actual),
                    hz_crudo_medio=hz_crudo_medio,
                    confianza=confianza,
                )
            )
        i = j

    return notas


def aplicar_filtros(
    notas: list[NotaDetectada], min_duracion_ms: float, min_confianza: float
) -> None:
    """Marca (sin eliminar) las notas que no pasan los filtros de limpieza, para
    que el reporte final las muestre igual con el motivo del descarte."""
    for nota in notas:
        motivos = []
        if nota.duracion_s * 1000 < min_duracion_ms:
            motivos.append(
                f"duración {nota.duracion_s * 1000:.0f}ms < mínimo {min_duracion_ms:.0f}ms"
            )
        if nota.confianza < min_confianza:
            motivos.append(
                f"confianza {nota.confianza:.2f} < mínimo {min_confianza:.2f}"
            )
        if motivos:
            nota.descartada = True
            nota.motivo_descarte = "; ".join(motivos)


def _snap(valor: float, permitidos: list[float]) -> float:
    return min(permitidos, key=lambda p: abs(p - valor))


def cuantizar_ritmo(notas: list[NotaDetectada], bpm: float) -> None:
    """Convierte segundos -> quarterLength con el tempo fijo dado, y snapea al
    grid/duraciones simples definidas arriba. Cuantización básica a propósito."""
    seg_por_negra = 60.0 / bpm
    duraciones_ql = [d for _, d in DURACIONES_PERMITIDAS]
    grid_valores = np.arange(0, 10_000, GRID_INICIO_QL)  # grid amplio, se recorta al usar

    for nota in notas:
        inicio_ql_crudo = nota.inicio_s / seg_por_negra
        duracion_ql_crudo = nota.duracion_s / seg_por_negra

        inicio_ql = round(inicio_ql_crudo / GRID_INICIO_QL) * GRID_INICIO_QL
        duracion_ql = _snap(duracion_ql_crudo, duraciones_ql)
        # Nunca cuantizar una nota a duración 0 -- si el crudo era muy corto pero
        # sobrevivió los filtros, al menos que quede en la figura más chica.
        if duracion_ql <= 0:
            duracion_ql = duraciones_ql[0]

        nombre = next(n for n, v in DURACIONES_PERMITIDAS if v == duracion_ql)

        nota.inicio_ql = float(inicio_ql)
        nota.duracion_ql = float(duracion_ql)
        nota.duracion_nombre = nombre


def construir_partitura(notas: list[NotaDetectada], bpm: float) -> music21.stream.Stream:
    """Arma un Stream de music21 con las notas sobrevivientes (no descartadas),
    completando los huecos entre ellas con Rest para no perder el timing
    relativo. Ortografía de la altura: primer intento razonable vía
    music21.pitch.Pitch(midi=...), no perfecta todavía (pedido explícito)."""
    stream = music21.stream.Stream()
    stream.append(music21.tempo.MetronomeMark(number=bpm))

    vivas = sorted((n for n in notas if not n.descartada), key=lambda n: n.inicio_ql)

    cursor_ql = 0.0
    for nota in vivas:
        hueco = nota.inicio_ql - cursor_ql
        if hueco > 0:
            stream.append(music21.note.Rest(quarterLength=hueco))
        elif hueco < 0:
            # Se solaparía con la anterior por el snapeo del grid -- no debería
            # pasar en el uso normal (monofónico, sin acordes), pero si pasa se
            # empuja la nota al cursor actual en vez de generar un Stream inválido.
            nota.inicio_ql = cursor_ql

        m21_note = music21.note.Note(music21.pitch.Pitch(midi=nota.midi_redondeado))
        m21_note.quarterLength = nota.duracion_ql
        stream.append(m21_note)
        cursor_ql = nota.inicio_ql + nota.duracion_ql

    return stream


def generar_reporte(
    notas: list[NotaDetectada], ruta_audio: str, bpm: float, out_path: Path
) -> None:
    vivas = [n for n in notas if not n.descartada]
    descartadas = [n for n in notas if n.descartada]

    lineas = []
    lineas.append(f"Reporte de transcripción -- {ruta_audio}")
    lineas.append(f"Tempo fijo asumido: {bpm} BPM")
    lineas.append(f"Notas detectadas: {len(notas)} | sobrevivientes: {len(vivas)} | descartadas: {len(descartadas)}")
    lineas.append("")
    lineas.append("NOTAS (sobrevivientes a los filtros)")
    lineas.append(
        f"{'#':>3}  {'inicio(s)':>9}  {'pitch':>6}  {'Hz crudo':>9}  {'duración':>22}  {'confianza':>9}"
    )
    lineas.append("-" * 70)
    for idx, nota in enumerate(vivas, start=1):
        p = music21.pitch.Pitch(midi=nota.midi_redondeado)
        lineas.append(
            f"{idx:>3}  {nota.inicio_s:>9.2f}  {p.nameWithOctave:>6}  {nota.hz_crudo_medio:>8.1f}Hz  "
            f"{nota.duracion_nombre:>17} ({nota.duracion_ql:.2f}ql)  {nota.confianza:>9.2f}"
        )

    if descartadas:
        lineas.append("")
        lineas.append("NOTAS DESCARTADAS POR LOS FILTROS")
        lineas.append(
            f"{'#':>3}  {'inicio(s)':>9}  {'pitch':>6}  {'Hz crudo':>9}  {'dur.(s)':>8}  {'confianza':>9}  motivo"
        )
        lineas.append("-" * 90)
        for idx, nota in enumerate(descartadas, start=1):
            p = music21.pitch.Pitch(midi=nota.midi_redondeado)
            lineas.append(
                f"{idx:>3}  {nota.inicio_s:>9.2f}  {p.nameWithOctave:>6}  {nota.hz_crudo_medio:>8.1f}Hz  "
                f"{nota.duracion_s:>8.3f}  {nota.confianza:>9.2f}  {nota.motivo_descarte}"
            )

    out_path.write_text("\n".join(lineas), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, help="Ruta al .wav con el silbido (sin click de fondo).")
    parser.add_argument("--bpm", required=True, type=float, help="Tempo fijo asumido, en BPM. Sin detección automática en esta fase.")
    parser.add_argument("--out-dir", default="salida", help="Carpeta de salida (se crea si no existe).")
    parser.add_argument("--fmin", default="C4", help="Frecuencia mínima esperada del silbido (nota o Hz). Default: C4.")
    parser.add_argument("--fmax", default="C7", help="Frecuencia máxima esperada del silbido (nota o Hz). Default: C7.")
    parser.add_argument("--min-duration-ms", type=float, default=80.0, help="Duración mínima de nota para no descartarla como ruido. Default: 80ms.")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="Confianza mínima (voiced_probs promedio) para no descartar la nota. Default: 0.5.")
    args = parser.parse_args()

    def a_hz(valor: str) -> float:
        try:
            return float(valor)
        except ValueError:
            return music21.pitch.Pitch(valor).frequency

    fmin_hz = a_hz(args.fmin)
    fmax_hz = a_hz(args.fmax)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Cargando audio: {args.audio}")
    y, sr = cargar_audio(args.audio)
    print(f"  sr={sr}Hz, duración={len(y) / sr:.2f}s")

    print(f"Detectando pitch con pYIN (rango {fmin_hz:.1f}Hz - {fmax_hz:.1f}Hz)...")
    f0, voiced_flag, voiced_probs, tiempos = detectar_pitch(y, sr, fmin_hz, fmax_hz)

    print("Detectando onsets...")
    onsets_s = detectar_onsets(y, sr)
    print(f"  {len(onsets_s)} onsets detectados")

    print("Segmentando en notas discretas...")
    notas = segmentar_notas(f0, voiced_probs, tiempos, onsets_s)
    print(f"  {len(notas)} notas candidatas")

    print(f"Aplicando filtros (min_duration={args.min_duration_ms}ms, min_confidence={args.min_confidence})...")
    aplicar_filtros(notas, args.min_duration_ms, args.min_confidence)
    sobrevivientes = sum(1 for n in notas if not n.descartada)
    print(f"  {sobrevivientes} notas sobreviven, {len(notas) - sobrevivientes} descartadas")

    print(f"Cuantizando ritmo a {args.bpm} BPM fijo...")
    cuantizar_ritmo(notas, args.bpm)

    print("Construyendo partitura (music21)...")
    partitura = construir_partitura(notas, args.bpm)

    musicxml_path = out_dir / "melodia.musicxml"
    partitura.write("musicxml", fp=str(musicxml_path))
    print(f"  MusicXML escrito en: {musicxml_path}")

    reporte_path = out_dir / "reporte.txt"
    generar_reporte(notas, args.audio, args.bpm, reporte_path)
    print(f"  Reporte escrito en: {reporte_path}")

    print("\nListo. Abrí el reporte.txt para comparar nota por nota contra lo que silbaste.")


if __name__ == "__main__":
    main()
