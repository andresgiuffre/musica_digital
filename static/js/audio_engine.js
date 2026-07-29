const AudioEngine = {
    sampler: null,
    isMuted: false,
    volume: -5,
    isReady: false,
    synthsFamilia: {},

    // Grupos de instrumentos por familia orquestal, para elegir un timbre genérico en
    // playSequence(). Orden deliberado: entradas más específicas antes que las genéricas
    // que las contienen como substring (ej. 'corno inglés' en madera, antes que 'corno'
    // en metal — si no, "Corno Inglés" matchearía mal).
    FAMILIAS_INSTRUMENTO: [
        [['piccolo', 'flautín', 'flautin', 'corno inglés', 'corno ingles', 'english horn', 'cor anglais',
          'oboe', 'clarinete bajo', 'bass clarinet', 'clarinet', 'clarinete', 'contrafagot', 'contrabassoon',
          'fagot', 'bassoon', 'flute', 'flauta', 'fl.'], 'madera'],
        [['horn', 'corno', 'trompa', 'trumpet', 'trompeta', 'trombón bajo', 'trombon bajo', 'bass trombone',
          'trombone', 'trombón', 'trombon', 'tuba'], 'metal'],
        [['violin', 'violín', 'viola', 'violoncello', 'violonchelo', 'cello', 'contrabass', 'contrabajo',
          'double bass', 'harp', 'arpa'], 'cuerda'],
    ],

    init() {
        this.bindSettingsUI();
    },

    bindSettingsUI() {
        const muteToggle = document.getElementById('audio-mute-toggle');
        const volSlider = document.getElementById('audio-volume-slider');

        if (muteToggle && !muteToggle.hasAttribute('data-bound')) {
            muteToggle.addEventListener('change', (e) => {
                this.isMuted = !e.target.checked;
                if (this.sampler) {
                    this.sampler.volume.value = this.isMuted ? -Infinity : this.volume;
                }
                Object.values(this.synthsFamilia).forEach(s => {
                    s.volume.value = this.isMuted ? -Infinity : this.volume;
                });
            });
            muteToggle.setAttribute('data-bound', 'true');
        }
        if (volSlider && !volSlider.hasAttribute('data-bound')) {
            volSlider.addEventListener('input', (e) => {
                const val = e.target.value;
                this.volume = val == 0 ? -Infinity : (val / 100) * 40 - 40;
                if (this.sampler && !this.isMuted) {
                    this.sampler.volume.value = this.volume;
                }
                if (!this.isMuted) {
                    Object.values(this.synthsFamilia).forEach(s => { s.volume.value = this.volume; });
                }
            });
            volSlider.setAttribute('data-bound', 'true');
        }
    },

    // Instrumento (nombre exacto de music21) -> familia orquestal, o null si no matchea
    // ninguna (queda con el piano por defecto).
    familiaInstrumento(nombre) {
        const n = (nombre || '').toLowerCase();
        for (const [keywords, familia] of this.FAMILIAS_INSTRUMENTO) {
            if (keywords.some(kw => n.includes(kw))) return familia;
        }
        return null;
    },

    // Timbres genéricos por familia armados con osciladores básicos de Tone.js — no hay
    // muestras reales de instrumentos orquestales disponibles, así que esto no busca
    // fidelidad exacta (un fagot no suena a fagot de verdad), solo diferenciarse
    // claramente del piano y entre familias. Se crean una sola vez y se reutilizan.
    obtenerSynthFamilia(familia) {
        if (this.synthsFamilia[familia]) return this.synthsFamilia[familia];

        const configs = {
            cuerda: { oscillator: { type: 'sawtooth' }, envelope: { attack: 0.15, decay: 0.2, sustain: 0.8, release: 0.6 } },
            madera: { oscillator: { type: 'triangle8' }, envelope: { attack: 0.05, decay: 0.1, sustain: 0.7, release: 0.3 } },
            metal: { oscillator: { type: 'square' }, envelope: { attack: 0.02, decay: 0.15, sustain: 0.6, release: 0.2 } },
        };
        const config = configs[familia];
        if (!config) return null;

        const synth = new Tone.PolySynth(Tone.Synth, config).toDestination();
        synth.volume.value = this.isMuted ? -Infinity : this.volume;
        this.synthsFamilia[familia] = synth;
        return synth;
    },
    
    preload() {
        this.init();
        if (this.sampler) return Promise.resolve();
        
        console.log("Preloading local piano samples with Tone.js...");
        
        return new Promise((resolve, reject) => {
            this.sampler = new Tone.Sampler({
                urls: {
                    "A0": "A0.mp3", "C1": "C1.mp3", "D#1": "Ds1.mp3", "F#1": "Fs1.mp3", "A1": "A1.mp3",
                    "C2": "C2.mp3", "D#2": "Ds2.mp3", "F#2": "Fs2.mp3", "A2": "A2.mp3",
                    "C3": "C3.mp3", "D#3": "Ds3.mp3", "F#3": "Fs3.mp3", "A3": "A3.mp3",
                    "C4": "C4.mp3", "D#4": "Ds4.mp3", "F#4": "Fs4.mp3", "A4": "A4.mp3",
                    "C5": "C5.mp3", "D#5": "Ds5.mp3", "F#5": "Fs5.mp3", "A5": "A5.mp3",
                    "C6": "C6.mp3", "D#6": "Ds6.mp3", "F#6": "Fs6.mp3", "A6": "A6.mp3",
                    "C7": "C7.mp3", "D#7": "Ds7.mp3", "F#7": "Fs7.mp3", "A7": "A7.mp3",
                    "C8": "C8.mp3"
                },
                baseUrl: "/static/audio/piano/",
                onload: () => {
                    this.isReady = true;
                    this.sampler.volume.value = this.isMuted ? -Infinity : this.volume;
                    this.sampler.toDestination();
                    console.log("Tone.js Piano sampler loaded successfully!");
                    resolve();
                },
                onerror: (err) => {
                    console.error("Tone.js failed to load samples", err);
                    reject(err);
                }
            });
        });
    },
    
    noteToMidiStr(noteStr) {
        let normalized = noteStr.replace('/', '').toUpperCase();
        if (!/\d/.test(normalized)) {
            normalized += '4';
        }
        return normalized;
    },
    
    playNote(note, startTime = null, duration = 1.0) {
        if (this.isMuted) return;
        
        if (!this.isReady) {
            this.preload().then(() => this.playNote(note, startTime, duration));
            return;
        }
        
        const midiNote = this.noteToMidiStr(note);
        const time = startTime !== null ? startTime : Tone.now();
        
        this.sampler.triggerAttackRelease(midiNote, duration, time);
    },
    
    playNotes(notes, startTime = null, duration = 1.2) {
        if (this.isMuted) return;
        
        if (!this.isReady) {
            this.preload().then(() => this.playNotes(notes, startTime, duration));
            return;
        }
        
        const time = startTime !== null ? startTime : Tone.now();
        const midiNotes = notes.map(n => this.noteToMidiStr(n));
        
        this.sampler.triggerAttackRelease(midiNotes, duration, time);
    },
    
    playInterval(note1, note2, harmonic = false, duration = 1.5) {
        if (this.isMuted) return;
        
        if (!this.isReady) {
            this.preload().then(() => this.playInterval(note1, note2, harmonic, duration));
            return;
        }
        
        const now = Tone.now();
        if (harmonic) {
            this.playNotes([note1, note2], now, duration);
        } else {
            this.playNote(note1, now, duration * 0.8);
            this.playNote(note2, now + duration * 0.8, duration * 0.8);
        }
    },
    
    playMelody(notes, noteDuration = 0.6, gap = 0.1) {
        if (this.isMuted) return;

        if (!this.isReady) {
            this.preload().then(() => this.playMelody(notes, noteDuration, gap));
            return;
        }

        const now = Tone.now();
        notes.forEach((note, idx) => {
            const time = now + idx * (noteDuration + gap);
            const midiNote = this.noteToMidiStr(note);
            this.sampler.triggerAttackRelease(midiNote, noteDuration, time);
        });
    },

    // events: [{ notes: [...], offset: beatsDesdeElInicio, duration: beats }, ...]
    // instrumento: nombre de la parte (exacto de music21) para elegir un timbre genérico
    // por familia — si no matchea ninguna, usa el piano por defecto.
    // Respeta ritmo real y acordes simultáneos, a diferencia de playMelody (espaciado fijo).
    playSequence(events, bpm = 100, instrumento = null) {
        if (this.isMuted) return;

        if (!this.isReady) {
            this.preload().then(() => this.playSequence(events, bpm, instrumento));
            return;
        }

        const familia = this.familiaInstrumento(instrumento);
        const voz = familia ? (this.obtenerSynthFamilia(familia) || this.sampler) : this.sampler;

        const secondsPerBeat = 60 / bpm;
        const now = Tone.now();
        events.forEach(ev => {
            if (!ev.notes || !ev.notes.length) return;
            const midiNotes = ev.notes.map(n => this.noteToMidiStr(n));
            const time = now + ev.offset * secondsPerBeat;
            const duration = Math.max(ev.duration * secondsPerBeat, 0.05);
            voz.triggerAttackRelease(midiNotes, duration, time);
        });
    }
};

document.addEventListener('DOMContentLoaded', () => {
    document.body.addEventListener('click', async () => {
        if(!AudioEngine.isReady) {
            if (window.Tone) {
                await Tone.start();
                AudioEngine.preload();
            }
        }
    }, { once: true });
});
