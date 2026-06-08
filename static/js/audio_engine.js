const AudioEngine = {
    ctx: null,
    
    init() {
        if (!this.ctx) {
            this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (this.ctx.state === 'suspended') {
            this.ctx.resume();
        }
    },
    
    noteToFreq(noteStr) {
        // Normalize strings like 'c/4' or 'C#5'
        let normalized = noteStr.toLowerCase().replace('/', '');
        
        const noteMap = {
            'c': 0, 'c#': 1, 'db': 1, 'd': 2, 'd#': 3, 'eb': 3, 'e': 4, 'f': 5, 'f#': 6, 'gb': 6,
            'g': 7, 'g#': 8, 'ab': 8, 'a': 9, 'a#': 10, 'bb': 10, 'b': 11
        };
        
        const match = normalized.match(/^([a-g]#?|db|eb|gb|ab|bb)?(\d)$/);
        if (!match) return 261.63; // Fallback to Do4
        
        const note = match[1];
        const octave = parseInt(match[2]);
        
        const semitonesFromC4 = noteMap[note] + (octave - 4) * 12;
        return 261.6256 * Math.pow(2, semitonesFromC4 / 12);
    },
    
    playFreq(freq, startTime, duration = 1.0, volume = 0.5) {
        try {
            this.init();
            const ctx = this.ctx;
            
            const osc1 = ctx.createOscillator();
            const osc2 = ctx.createOscillator();
            const osc3 = ctx.createOscillator();
            const gainNode = ctx.createGain();
            const filter = ctx.createBiquadFilter();
            
            // Piano synthesis setup
            osc1.type = 'triangle';
            osc1.frequency.setValueAtTime(freq, startTime);
            
            osc2.type = 'sine';
            osc2.frequency.setValueAtTime(freq * 2, startTime);
            
            osc3.type = 'sine';
            osc3.frequency.setValueAtTime(freq * 3, startTime);
            
            // Lowpass filter for warmer sound
            filter.type = 'lowpass';
            filter.frequency.setValueAtTime(freq * 4, startTime);
            filter.frequency.exponentialRampToValueAtTime(freq * 1.5, startTime + duration);
            
            // Gain Node envelope
            gainNode.gain.setValueAtTime(0, startTime);
            gainNode.gain.linearRampToValueAtTime(volume, startTime + 0.015); // Attack
            gainNode.gain.exponentialRampToValueAtTime(volume * 0.4, startTime + 0.25); // Decay
            gainNode.gain.setValueAtTime(volume * 0.4, startTime + duration - 0.15); // Sustain
            gainNode.gain.exponentialRampToValueAtTime(0.0001, startTime + duration); // Release
            
            // Gain settings for harmonics
            const gainHarmonic1 = ctx.createGain();
            const gainHarmonic2 = ctx.createGain();
            
            gainHarmonic1.gain.setValueAtTime(volume * 0.35, startTime);
            gainHarmonic1.gain.exponentialRampToValueAtTime(0.0001, startTime + 0.35);
            
            gainHarmonic2.gain.setValueAtTime(volume * 0.15, startTime);
            gainHarmonic2.gain.exponentialRampToValueAtTime(0.0001, startTime + 0.2);
            
            osc1.connect(gainNode);
            osc2.connect(gainHarmonic1);
            osc3.connect(gainHarmonic2);
            
            gainHarmonic1.connect(gainNode);
            gainHarmonic2.connect(gainNode);
            
            gainNode.connect(filter);
            filter.connect(ctx.destination);
            
            osc1.start(startTime);
            osc2.start(startTime);
            osc3.start(startTime);
            
            osc1.stop(startTime + duration);
            osc2.stop(startTime + duration);
            osc3.stop(startTime + duration);
        } catch (e) {
            console.error("Audio Engine playback failed:", e);
        }
    },
    
    playNote(note, startTime = 0, duration = 1.0) {
        this.init();
        if (startTime === 0) {
            startTime = this.ctx.currentTime;
        }
        const freq = this.noteToFreq(note);
        this.playFreq(freq, startTime, duration);
    },
    
    playNotes(notes, startTime = 0, duration = 1.2) {
        this.init();
        if (startTime === 0) {
            startTime = this.ctx.currentTime;
        }
        const vol = 0.5 / Math.max(1, notes.length);
        notes.forEach(note => {
            const freq = this.noteToFreq(note);
            this.playFreq(freq, startTime, duration, vol);
        });
    },
    
    playInterval(note1, note2, harmonic = false, duration = 1.0) {
        this.init();
        const now = this.ctx.currentTime;
        if (harmonic) {
            this.playNotes([note1, note2], now, duration);
        } else {
            this.playNote(note1, now, duration * 0.8);
            this.playNote(note2, now + duration * 0.8, duration * 0.8);
        }
    },
    
    playMelody(notes, noteDuration = 0.6, gap = 0.1) {
        this.init();
        const now = this.ctx.currentTime;
        notes.forEach((note, idx) => {
            const time = now + idx * (noteDuration + gap);
            this.playNote(note, time, noteDuration);
        });
    }
};
