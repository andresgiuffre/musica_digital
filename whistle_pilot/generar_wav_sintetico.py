"""
Genera un .wav sintético (tonos senoidales puros con vibrato leve, sin click de
metrónomo) para probar transcribe.py de punta a punta ANTES de depender de que
alguien grabe un silbido real.

La melodía de prueba incluye a propósito:
- Un par de notas repetidas consecutivas (E5, E5) con un micro-reataque entre
  ellas -- para poner a prueba la separación por onset, no solo por cambio de
  pitch (el caso que específicamente pidió el usuario validar).
- Una pausa corta entre frases -- para probar el manejo de silencio.
- Vibrato leve -- un silbido real nunca es un tono perfectamente estable.

Uso:
    python generar_wav_sintetico.py [--out melodia_sintetica.wav] [--bpm 90]
"""
import argparse

import numpy as np
import soundfile as sf

SR = 44100

# (nota_music21, duracion_en_negras). None = silencio.
# Incluye C5-D5-E5-E5(repetida)-G5-silencio-F5-D5-C5
MELODIA = [
    ("C5", 1.0),
    ("D5", 1.0),
    ("E5", 0.5),
    ("E5", 0.5),  # repetida a propósito, misma altura que la anterior
    ("G5", 1.0),
    (None, 0.5),  # pausa corta
    ("F5", 1.0),
    ("D5", 1.0),
    ("C5", 2.0),
]


def nota_a_hz(nombre: str) -> float:
    import music21

    return music21.pitch.Pitch(nombre).frequency


def generar_tono(freq_hz: float, duracion_s: float, sr: int, con_reataque: bool = False) -> np.ndarray:
    """Tono senoidal con vibrato leve (~5Hz, +-15 cents) y envolvente ADSR simple
    para que no suene como un beep de laboratorio. Si con_reataque=True, mete un
    micro-dip de amplitud al principio para simular un pequeño re-soplido -- así
    el onset detector tiene algo real que encontrar incluso repitiendo la misma
    altura."""
    n = int(sr * duracion_s)
    t = np.arange(n) / sr

    vibrato_hz = 5.0
    vibrato_cents = 15.0
    vibrato_mult = 2 ** ((vibrato_cents * np.sin(2 * np.pi * vibrato_hz * t)) / 1200)
    freq_t = freq_hz * vibrato_mult

    fase = 2 * np.pi * np.cumsum(freq_t) / sr
    señal = np.sin(fase)

    # Envolvente: ataque/release cortos para evitar clicks al pegar los tramos.
    attack_n = min(int(sr * 0.02), n // 4) or 1
    release_n = min(int(sr * 0.03), n // 4) or 1
    env = np.ones(n)
    env[:attack_n] = np.linspace(0, 1, attack_n)
    env[-release_n:] = np.linspace(1, 0, release_n)

    if con_reataque:
        dip_n = min(int(sr * 0.04), n // 3) or 1
        env[:dip_n] *= np.linspace(0.15, 1.0, dip_n)

    return (señal * env * 0.5).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="melodia_sintetica.wav")
    parser.add_argument("--bpm", type=float, default=90.0)
    args = parser.parse_args()

    seg_por_negra = 60.0 / args.bpm
    partes = []
    nota_anterior_pitch = None

    for nombre, duracion_negras in MELODIA:
        duracion_s = duracion_negras * seg_por_negra
        if nombre is None:
            partes.append(np.zeros(int(SR * duracion_s), dtype=np.float32))
            nota_anterior_pitch = None
            continue

        con_reataque = nombre == nota_anterior_pitch
        freq = nota_a_hz(nombre)
        partes.append(generar_tono(freq, duracion_s, SR, con_reataque=con_reataque))
        nota_anterior_pitch = nombre

    audio = np.concatenate(partes)
    sf.write(args.out, audio, SR)
    print(f"Escrito {args.out} ({len(audio) / SR:.2f}s @ {SR}Hz, {args.bpm} BPM)")
    print("Melodía: " + " - ".join(n or "(pausa)" for n, _ in MELODIA))


if __name__ == "__main__":
    main()
