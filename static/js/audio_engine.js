const AudioEngine = {
    sampler: null,
    isMuted: false,
    volume: -5,
    isReady: false,
    
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
            });
            volSlider.setAttribute('data-bound', 'true');
        }
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
    // Respeta ritmo real y acordes simultáneos, a diferencia de playMelody (espaciado fijo).
    playSequence(events, bpm = 100) {
        if (this.isMuted) return;

        if (!this.isReady) {
            this.preload().then(() => this.playSequence(events, bpm));
            return;
        }

        const secondsPerBeat = 60 / bpm;
        const now = Tone.now();
        events.forEach(ev => {
            if (!ev.notes || !ev.notes.length) return;
            const midiNotes = ev.notes.map(n => this.noteToMidiStr(n));
            const time = now + ev.offset * secondsPerBeat;
            const duration = Math.max(ev.duration * secondsPerBeat, 0.05);
            this.sampler.triggerAttackRelease(midiNotes, duration, time);
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
