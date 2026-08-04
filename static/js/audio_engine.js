const AudioEngine = {
    sampler: null,
    isMuted: false,
    volume: -5,
    isReady: false,
    synthsInstrumento: {},
    masterBus: null,

    // Atenuación fija (dB) solo para los synths sintéticos (no el sampler de piano,
    // que ya suena bien): un PolySynth sawtooth en polifonía de acordes x 5 partes
    // suma mucha más energía que el piano muestreado, así que arranca con menos
    // margen antes de llegar al compressor/limiter del master.
    ATENUACION_SYNTH_DB: -8,

    // Compressor + Limiter compartidos, entre CUALQUIER voz (sampler o synth) y
    // Destination -- así ninguna combinación de voces simultáneas puede clipear,
    // sin importar cuántas partes/acordes suenen a la vez. Se arma una sola vez y
    // se cachea (igual que synthsInstrumento).
    _construirMasterBus() {
        if (!this.masterBus) {
            const compressor = new Tone.Compressor({ threshold: -18, ratio: 4, attack: 0.003, release: 0.25 });
            const limiter = new Tone.Limiter(-1);
            compressor.connect(limiter);
            limiter.toDestination();
            this.masterBus = compressor;
        }
        return this.masterBus;
    },

    _volumenSampler() {
        return this.isMuted ? -Infinity : this.volume;
    },

    _volumenSynth() {
        return this.isMuted ? -Infinity : this.volume + this.ATENUACION_SYNTH_DB;
    },

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
                    this.sampler.volume.value = this._volumenSampler();
                }
                Object.values(this.synthsInstrumento).forEach(s => {
                    s.volume.value = this._volumenSynth();
                });
            });
            muteToggle.setAttribute('data-bound', 'true');
        }
        if (volSlider && !volSlider.hasAttribute('data-bound')) {
            volSlider.addEventListener('input', (e) => {
                const val = e.target.value;
                this.volume = val == 0 ? -Infinity : (val / 100) * 40 - 40;
                if (this.sampler && !this.isMuted) {
                    this.sampler.volume.value = this._volumenSampler();
                }
                if (!this.isMuted) {
                    Object.values(this.synthsInstrumento).forEach(s => { s.volume.value = this._volumenSynth(); });
                }
            });
            volSlider.setAttribute('data-bound', 'true');
        }
    },

    // Instrumento (nombre exacto de music21) -> familia orquestal, o null si no matchea
    // ninguna (queda con el piano por defecto).
    familiaInstrumento(nombre) {
        // nombre nulo/vacío es una señal deliberada de "usar el piano por defecto"
        // (ej. reproducir el fragmento original) — no es un nombre que falló el
        // matcheo, así que no amerita advertencia.
        if (!nombre) return null;
        const n = nombre.toLowerCase();
        for (const [keywords, familia] of this.FAMILIAS_INSTRUMENTO) {
            if (keywords.some(kw => n.includes(kw))) return familia;
        }
        console.warn(`AudioEngine: "${nombre}" no matchea ninguna familia conocida — va a sonar piano.`);
        return null;
    },

    // Configuración de timbre genérico por familia orquestal — no hay muestras reales de
    // instrumentos disponibles, así que esto no busca fidelidad exacta (un fagot no suena
    // a fagot de verdad), solo diferenciarse claramente del piano y entre familias.
    CONFIGS_FAMILIA: {
        cuerda: { oscillator: { type: 'sawtooth' }, envelope: { attack: 0.15, decay: 0.2, sustain: 0.8, release: 0.6 } },
        madera: { oscillator: { type: 'triangle8' }, envelope: { attack: 0.05, decay: 0.1, sustain: 0.7, release: 0.3 } },
        metal: { oscillator: { type: 'square' }, envelope: { attack: 0.02, decay: 0.15, sustain: 0.6, release: 0.2 } },
    },

    // Un sintetizador por INSTRUMENTO (no por familia): varias partes de la misma familia
    // (ej. Violín I, Violín II, Viola, Violonchelo, Contrabajo son las 5 'cuerda') pueden
    // sonar simultáneamente — compartir una sola voz de Tone.PolySynth entre todas hacía
    // que sonaran indistinguibles entre sí y saturaba una única instancia al reproducirlas
    // juntas (bug real encontrado en el ejercicio de orquestación). La familia solo elige
    // qué config de oscilador/envelope usar como plantilla; cada nombre de instrumento
    // recibe su propia instancia, cacheada por nombre.
    obtenerSynthInstrumento(nombre, familia) {
        if (this.synthsInstrumento[nombre]) return this.synthsInstrumento[nombre];

        const config = this.CONFIGS_FAMILIA[familia];
        if (!config) return null;

        const synth = new Tone.PolySynth(Tone.Synth, config).connect(this._construirMasterBus());
        synth.volume.value = this._volumenSynth();
        this.synthsInstrumento[nombre] = synth;
        return synth;
    },

    // --- Muestras reales de cuerda (tonejs-instruments, ver <script> en base.html,
    // pineado a un commit concreto vía jsDelivr). Solo cubre violin/cello/contrabass —
    // viento/metal siguen con el synth de familia de arriba. Si el nombre no matchea
    // ninguna clave, o el CDN falló, playSequence() cae solo al synth — nunca silencio. ---

    MUESTRA_INSTRUMENTO: [
        [['contrabass', 'contrabajo', 'double bass'], 'contrabass'],
        [['violoncello', 'violonchelo', 'cello'], 'cello'],
        // Sin muestras propias de viola en la librería: se aproxima con cello — cubre el
        // extremo grave del rango cómodo de Viola (C3, ver RANGOS_COMODOS en views.py) sin
        // estirar samples (el violín más grave muestreado es A3) y es tímbricamente más
        // cercano (oscuro/cálido) que violín.
        [['viola'], 'cello'],
        [['violin', 'violín'], 'violin'],
    ],

    // OJO: NO jsDelivr acá, a propósito -- Tone.js (ToneAudioBuffer.load) arma la URL de
    // cada muestra con `pathname.split('/').map(encodeURIComponent).join('/')`, que
    // codifica el '@' del pin de versión de jsDelivr (.../tonejs-instruments@<hash>/...)
    // a '%40' y rompe el ruteo de jsDelivr (400 confirmado en la práctica). El <script>
    // del loader en base.html sí puede usar jsDelivr porque un <script src> normal no
    // pasa por ese código de Tone.js. raw.githubusercontent.com no tiene ningún '@' en
    // el path (el hash es un segmento más), así que no le afecta el bug, y apunta al
    // mismo commit pineado -- mismo contenido inmutable, verificado con curl (200,
    // audio/mpeg, CORS *) en las notas que antes fallaban.
    CDN_MUESTRAS_CUERDA_BASE_URL: 'https://raw.githubusercontent.com/nbrosowsky/tonejs-instruments/622c2f1c32c8cfce4158ddc3eb26e518ddef37e5/samples/',
    TIMEOUT_CARGA_MUESTRAS_MS: 15000,

    samplesInstrumento: {},  // clave (violin/cello/contrabass) -> Tone.Sampler ya cargado
    samplesCargando: {},     // clave -> Promise en curso, para no disparar 2 cargas a la vez

    // Instrumento (nombre exacto de music21) -> clave de tonejs-instruments, o null si no
    // hay muestra real para ese instrumento (usará synth).
    muestraInstrumento(nombre) {
        if (!nombre) return null;
        const n = nombre.toLowerCase();
        for (const [keywords, clave] of this.MUESTRA_INSTRUMENTO) {
            if (keywords.some(kw => n.includes(kw))) return clave;
        }
        return null;
    },

    // Resuelve a un Tone.Sampler cargado, o null si no hay CDN disponible o se cumplió el
    // timeout de carga. Nunca rechaza -- el llamador siempre puede seguir con el synth.
    obtenerSamplerInstrumento(clave) {
        if (this.samplesInstrumento[clave]) return Promise.resolve(this.samplesInstrumento[clave]);
        if (this.samplesCargando[clave]) return this.samplesCargando[clave];

        if (typeof SampleLibrary === 'undefined') {
            console.warn(`AudioEngine: tonejs-instruments no está disponible (¿falló el CDN?) — "${clave}" va a sonar con synth.`);
            return Promise.resolve(null);
        }

        const promesa = new Promise((resolve) => {
            let resuelto = false;
            const timeoutId = setTimeout(() => {
                if (resuelto) return;
                resuelto = true;
                console.warn(`AudioEngine: timeout cargando muestras de "${clave}" desde el CDN — va a sonar con synth.`);
                resolve(null);
            }, this.TIMEOUT_CARGA_MUESTRAS_MS);

            let sampler;
            try {
                sampler = SampleLibrary.load({
                    instruments: clave,
                    ext: '.mp3',
                    baseUrl: this.CDN_MUESTRAS_CUERDA_BASE_URL,
                    onload: () => {
                        if (resuelto) return;
                        resuelto = true;
                        clearTimeout(timeoutId);
                        this.samplesInstrumento[clave] = sampler;
                        resolve(sampler);
                    },
                });
            } catch (err) {
                resuelto = true;
                clearTimeout(timeoutId);
                console.warn(`AudioEngine: error creando el sampler de "${clave}" — va a sonar con synth.`, err);
                resolve(null);
                return;
            }
            sampler.connect(this._construirMasterBus());
            sampler.volume.value = this._volumenSampler();
        }).finally(() => {
            delete this.samplesCargando[clave];
        });

        this.samplesCargando[clave] = promesa;
        return promesa;
    },

    // API pública para que la UI precargue y muestre un estado de carga antes de
    // reproducir (lazy: la descarga real recién se dispara la primera vez que se llama
    // para cada instrumento). Resuelve siempre, nunca rechaza.
    precargarMuestraInstrumento(nombre) {
        const clave = this.muestraInstrumento(nombre);
        if (!clave) return Promise.resolve(null);
        return this.obtenerSamplerInstrumento(clave);
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
                    this.sampler.volume.value = this._volumenSampler();
                    this.sampler.connect(this._construirMasterBus());
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

        // Preferí la muestra real ya cargada (Capa 2); si no está lista todavía (nadie
        // llamó precargarMuestraInstrumento, o el CDN falló/está tardando), caé al synth
        // de familia (Capa 1) -- nunca queda en silencio ni bloquea esta llamada.
        const claveMuestra = this.muestraInstrumento(instrumento);
        let voz;
        if (claveMuestra && this.samplesInstrumento[claveMuestra]) {
            voz = this.samplesInstrumento[claveMuestra];
        } else {
            const familia = this.familiaInstrumento(instrumento);
            voz = familia ? (this.obtenerSynthInstrumento(instrumento, familia) || this.sampler) : this.sampler;
        }

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
