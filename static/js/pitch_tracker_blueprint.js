/**
 * PITCH TRACKER BLUEPRINT
 * Preparación para futuras funciones de evaluación con micrófono.
 * 
 * Este archivo contiene la estructura base para inicializar el Web Audio API,
 * pedir permisos al usuario para el micrófono, y detectar la frecuencia (Pitch)
 * mediante algoritmos de autocorrelación como YIN o McLeod.
 * 
 * NO INTEGRAR TODAVÍA en la UI principal. Solo para uso futuro.
 */

class PitchEvaluator {
    constructor() {
        this.audioContext = null;
        this.analyser = null;
        this.mediaStreamSource = null;
        this.isRecording = false;
        this.pitchUpdateInterval = null;
    }

    /**
     * Pide permisos al navegador e inicializa el AudioContext para leer el micrófono.
     */
    async initializeMicrophone() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 2048;

            this.mediaStreamSource = this.audioContext.createMediaStreamSource(stream);
            this.mediaStreamSource.connect(this.analyser);
            
            console.log("Micrófono inicializado correctamente.");
            return true;
        } catch (error) {
            console.error("Error al acceder al micrófono:", error);
            return false;
        }
    }

    /**
     * Inicia el bucle de detección de tono.
     */
    startTracking() {
        if (!this.analyser) {
            console.warn("Analizador no inicializado. Llama a initializeMicrophone primero.");
            return;
        }
        this.isRecording = true;
        this.pitchUpdateInterval = setInterval(() => this.detectPitch(), 50); // 20 fps
    }

    /**
     * Detiene la detección de tono.
     */
    stopTracking() {
        this.isRecording = false;
        if (this.pitchUpdateInterval) {
            clearInterval(this.pitchUpdateInterval);
        }
    }

    /**
     * Función principal del algoritmo de autocorrelación (Simplificado).
     * En el futuro, integrar una librería como `ml5.js` (PitchDetection) 
     * o `pitchfinder` (YIN/AMDF) para mayor precisión polifónica.
     */
    detectPitch() {
        const buffer = new Float32Array(this.analyser.fftSize);
        this.analyser.getFloatTimeDomainData(buffer);

        const pitch = this.autoCorrelate(buffer, this.audioContext.sampleRate);
        
        if (pitch !== -1) {
            const note = this.freqToNote(pitch);
            // Futuro: Enviar evento a la UI o comparar con la nota que el cursor de OSMD dice que debe sonar
            console.log(`Frecuencia detectada: ${pitch.toFixed(2)} Hz -> Nota: ${note}`);
        }
    }

    /**
     * Convierte una frecuencia en Hertz a una nota musical estándar (C4, G5, etc.)
     */
    freqToNote(freq) {
        const noteStrings = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
        // A4 = 440 Hz es el estándar MIDI 69
        const noteNum = 12 * (Math.log(freq / 440) / Math.log(2));
        const midiNote = Math.round(noteNum) + 69;
        
        const octave = Math.floor(midiNote / 12) - 1;
        const noteName = noteStrings[midiNote % 12];
        
        return `${noteName}${octave}`;
    }

    /**
     * Algoritmo básico de Autocorrelación para encontrar la frecuencia fundamental.
     */
    autoCorrelate(buf, sampleRate) {
        let size = buf.length;
        let rms = 0;
        
        // Verificar volumen mínimo (Gate)
        for (let i = 0; i < size; i++) {
            let val = buf[i];
            rms += val * val;
        }
        rms = Math.sqrt(rms / size);
        if (rms < 0.01) return -1; // Silencio

        // ... (Aquí iría la implementación completa de YIN/AMDF)
        // Por propósitos del blueprint, retornamos A4 estático simulado.
        return 440.0;
    }
}

// Exportación sugerida para módulos futuros:
// export default PitchEvaluator;
