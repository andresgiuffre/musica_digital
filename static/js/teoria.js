const TheoryKB = {
    notesMap: { 'c': 'Do', 'd': 'Re', 'e': 'Mi', 'f': 'Fa', 'g': 'Sol', 'a': 'La', 'b': 'Si' },
    accMap: { 'n': 'natural', '#': 'sostenido', 'b': 'bemol', '##': 'doble sostenido', 'bb': 'doble bemol' },
    qualityMap: { 'M': 'Mayor', 'm': 'Menor', 'J': 'Justa', 'A': 'Aumentada', 'd': 'Disminuida', 'AA': 'Doble Aumentada', 'dd': 'Doble Disminuida' },
    intervalMap: { 2: 'Segunda', 3: 'Tercera', 4: 'Cuarta', 5: 'Quinta', 6: 'Sexta', 7: 'Séptima', 8: 'Octava' },

    staffPositions: {
        'c/3': 'primera línea adicional inferior (octava 3)',
        'd/3': 'espacio debajo de primera línea adicional inferior',
        'e/3': 'tercera línea adicional inferior',
        'f/3': 'espacio debajo de tercera línea adicional inferior',
        'g/3': 'segunda línea adicional inferior',
        'a/3': 'espacio debajo de segunda línea adicional inferior',
        'b/3': 'espacio debajo de la primera línea adicional inferior', 
        'c/4': 'primera línea adicional inferior',
        'd/4': 'espacio justo debajo del pentagrama',
        'e/4': 'primera línea del pentagrama',
        'f/4': 'primer espacio del pentagrama',
        'g/4': 'segunda línea del pentagrama',
        'a/4': 'segundo espacio del pentagrama',
        'b/4': 'tercera línea del pentagrama',
        'c/5': 'tercer espacio del pentagrama',
        'd/5': 'cuarta línea del pentagrama',
        'e/5': 'cuarto espacio del pentagrama',
        'f/5': 'quinta línea del pentagrama',
        'g/5': 'espacio justo sobre el pentagrama',
        'a/5': 'primera línea adicional superior',
        'b/5': 'espacio sobre primera línea adicional superior',
        'c/6': 'segunda línea adicional superior'
    },

    getNoteExplanation: function(noteStr, accStr, mode) {
        // noteStr format: 'c/4'
        const letter = noteStr.charAt(0);
        const name = this.notesMap[letter];
        const accName = this.accMap[accStr];
        const position = this.staffPositions[noteStr] || 'fuera del rango central';

        let explanation = `La nota ${name} se encuentra en la <strong>${position}</strong> en clave de Sol. `;
        
        if (accStr === '#') {
            explanation += `<br><br>El sostenido eleva la altura un semitono respecto de ${name} natural.`;
        } else if (accStr === 'b') {
            explanation += `<br><br>El bemol reduce la altura un semitono respecto de ${name} natural.`;
        } else if (accStr === '##') {
            explanation += `<br><br>El doble sostenido eleva la altura dos semitonos (un tono entero) respecto de ${name} natural.`;
        } else if (accStr === 'bb') {
            explanation += `<br><br>El doble bemol reduce la altura dos semitonos (un tono entero) respecto de ${name} natural.`;
        } else {
            explanation += `<br><br>Al ser una nota natural, no tiene alteraciones.`;
        }

        if (mode === 'learning') {
            explanation += `<br><br>💡 <strong>Observación Teórica:</strong> Cada línea y espacio del pentagrama representa una nota natural consecutiva. Las alteraciones (♯, ♭) se colocan a la izquierda de la nota para modificar su altura sin cambiar su posición gráfica en el pentagrama.`;
        }

        return explanation;
    },

    getIntervalExplanation: function(degree, quality, mode, isAuditory) {
        const intervalName = this.qualityMap[quality] + ' ' + this.intervalMap[degree];
        
        // Exact semitones calc
        const baseSemitones = {2:2, 3:4, 4:5, 5:7, 6:9, 7:11, 8:12}[degree];
        let diff = 0;
        const perfect = [1,4,5,8].includes(degree);
        
        if (quality === 'm') diff = -1;
        if (quality === 'd') diff = perfect ? -1 : -2;
        if (quality === 'dd') diff = perfect ? -2 : -3;
        if (quality === 'A') diff = 1;
        if (quality === 'AA') diff = 2;
        
        const totalSemitones = baseSemitones + diff;
        
        let explanation = `<ul style="margin-top: 0.5rem; margin-bottom: 1rem; padding-left: 1.5rem; text-align: left;">`;
        explanation += `<li><strong>Distancia diatónica:</strong> ${degree}</li>`;
        explanation += `<li><strong>Semitonos exactos:</strong> ${totalSemitones}</li>`;
        explanation += `</ul>`;
        
        // Theory rule
        let rule = "";
        if (quality === 'M') rule = `Es un intervalo mayor estándar, originado de la escala mayor natural.`;
        if (quality === 'J') rule = `Es un intervalo justo (perfecto), muy consonante y fundamental en la armonía.`;
        if (quality === 'm') rule = `Una ${this.intervalMap[degree]} Menor se obtiene reduciendo un semitono respecto de una Mayor.`;
        if (quality === 'A') rule = `Se obtiene elevando un semitono respecto de una ${perfect ? 'Justa' : 'Mayor'}.`;
        if (quality === 'd') rule = `Se obtiene reduciendo un semitono respecto de una ${perfect ? 'Justa' : 'Menor'}.`;
        
        explanation += `<p style="text-align: left;">${rule}</p>`;

        if (totalSemitones === 6) {
            explanation += `<p style="text-align: left; color: var(--primary);">También es conocida como <strong>Tritono</strong>, dividiendo la octava exactamente a la mitad.</p>`;
        }

        if (isAuditory) {
            let tip = "";
            if (degree === 5 && quality === 'J') tip = "Suena muy estable y abierto, como el comienzo del tema de Star Wars o Twinkle Twinkle.";
            if (degree === 4 && quality === 'J') tip = "Suena estable pero con una ligera tensión que pide resolver, típico del himno de bodas.";
            if (degree === 3 && quality === 'm') tip = "Tiene un carácter triste o melancólico.";
            if (degree === 3 && quality === 'M') tip = "Tiene un carácter brillante y alegre.";
            if (degree === 2 && quality === 'm') tip = "Es sumamente tenso y disonante, como la melodía de Tiburón.";
            if (degree === 6 && quality === 'M') tip = "Suena muy melódico y romántico (ej. My Bonnie Lies Over the Ocean).";
            if (totalSemitones === 6) tip = "Suena inestable, oscuro y disonante, propio del tritono (Los Simpson intro).";
            if (degree === 8) tip = "Ambas notas suenan como la misma pero a diferente altura (octava).";
            
            if (tip) explanation += `<p style="text-align: left; margin-top: 1rem;">🎧 <em>Consejo Auditivo:</em> ${tip}</p>`;
        }

        if (mode === 'learning') {
            explanation += `<div style="text-align: left; background: rgba(79, 70, 229, 0.05); padding: 1rem; border-radius: 0.5rem; margin-top: 1rem; border-left: 3px solid var(--primary);">💡 <strong>Observación Teórica:</strong> Los intervalos se definen por dos componentes: su número (distancia diatónica contando el nombre de las notas) y su calidad (ajuste fino en semitonos alterando esas notas).</div>`;
        }

        return explanation;
    },

    getMelodyExplanation: function(correctArr, guessedArr, mode) {
        let explanation = "";
        
        if (correctArr[0] === guessedArr[0] && correctArr[1] === guessedArr[2] && correctArr[2] === guessedArr[1]) {
            explanation = "La segunda y tercera nota fueron intercambiadas.";
        } else if (correctArr[1] === guessedArr[1] && correctArr[0] === guessedArr[2] && correctArr[2] === guessedArr[0]) {
            explanation = "La primera y tercera nota fueron intercambiadas.";
        } else if (correctArr[0] === guessedArr[0] && correctArr[1] === guessedArr[1]) {
            explanation = "Acertaste las dos primeras notas, pero fallaste en la última.";
        } else if (correctArr[0] === guessedArr[0]) {
            explanation = "La primera nota fue correcta, pero la dirección o el salto de la melodía posterior fue diferente.";
        } else {
            explanation = "La melodía completa tenía una forma o contorno diferente al que elegiste.";
        }

        let ret = `<p style="text-align: left;"><strong>Análisis del error:</strong> ${explanation}</p>`;

        if (mode === 'learning') {
            ret += `<div style="text-align: left; background: rgba(79, 70, 229, 0.05); padding: 1rem; border-radius: 0.5rem; margin-top: 1rem; border-left: 3px solid var(--primary);">💡 <strong>Consejo:</strong> Presta atención a la "forma" de la melodía (si sube, si baja, o si repite la nota) antes de intentar identificar las notas exactas. Puedes guiarte dibujando la curva en el aire con tu mano mientras escuchas.</div>`;
        }

        return ret;
    },

    getReadingExplanation: function(timeSignature, totalBeats, mode) {
        let explanation = "";
        let numerator = parseInt(timeSignature.split('/')[0]);
        let denominator = parseInt(timeSignature.split('/')[1]);

        let beatsPerMeasure = numerator;
        let beatUnit = denominator === 4 ? "negras" : denominator === 8 ? "corcheas" : "blancas";

        explanation += `<ul style="margin-top: 0.5rem; margin-bottom: 1rem; padding-left: 1.5rem; text-align: left;">`;
        explanation += `<li><strong>Compás:</strong> ${timeSignature}</li>`;
        explanation += `<li><strong>Duración Total Reproducida:</strong> ${totalBeats} tiempos</li>`;
        explanation += `</ul>`;

        let rule = `El compás de ${timeSignature} indica que hay ${beatsPerMeasure} tiempos por compás, y la unidad de tiempo es equivalente a ${beatUnit}.`;
        explanation += `<p style="text-align: left;">${rule}</p>`;

        if (mode === 'learning') {
            explanation += `<div style="text-align: left; background: rgba(79, 70, 229, 0.05); padding: 1rem; border-radius: 0.5rem; margin-top: 1rem; border-left: 3px solid var(--primary);">💡 <strong>Observación Teórica:</strong> El número superior del compás (numerador) indica la cantidad de tiempos. El inferior (denominador) indica la figura que vale un tiempo (4 = negra, 8 = corchea, 2 = blanca).</div>`;
        }

        return explanation;
    },

    getBeatsExplanation: function(timeSignature, mode) {
        let explanation = "";
        let numerator = parseInt(timeSignature.split('/')[0]);
        let rule = "";

        explanation += `<ul style="margin-top: 0.5rem; margin-bottom: 1rem; padding-left: 1.5rem; text-align: left;">`;
        explanation += `<li><strong>Compás utilizado:</strong> ${timeSignature}</li>`;
        
        if (numerator === 2) {
            explanation += `<li><strong>Distribución:</strong> Tiempo 1 = fuerte, Tiempo 2 = débil</li>`;
            rule = `Por eso las notas ubicadas en el tiempo 1 fueron consideradas acentuadas.`;
        } else if (numerator === 3) {
            explanation += `<li><strong>Distribución:</strong> Tiempo 1 = fuerte, Tiempos 2 y 3 = débiles</li>`;
            rule = `Por eso las notas ubicadas en el tiempo 1 fueron consideradas acentuadas.`;
        } else if (numerator === 4) {
            explanation += `<li><strong>Distribución:</strong> Tiempo 1 = fuerte, Tiempo 2 = débil, Tiempo 3 = semifuerte, Tiempo 4 = débil</li>`;
            rule = `Por eso las notas ubicadas en los tiempos 1 y 3 fueron consideradas acentuadas.`;
        } else {
            explanation += `<li><strong>Distribución:</strong> Tiempo 1 = fuerte, el resto varía según subdivisión</li>`;
            rule = `El primer tiempo de todo compás lleva el acento principal.`;
        }
        explanation += `</ul>`;

        explanation += `<p style="text-align: left;">${rule}</p>`;

        if (mode === 'learning') {
            explanation += `<div style="text-align: left; background: rgba(79, 70, 229, 0.05); padding: 1rem; border-radius: 0.5rem; margin-top: 1rem; border-left: 3px solid var(--primary);">💡 <strong>Observación Teórica:</strong> Entender dónde recaen los acentos fuertes naturales del compás te ayudará inmensamente al fraseo musical y a entender la estructura rítmica real, en lugar de leer las notas como si fueran un metrónomo plano.</div>`;
        }

        return explanation;
    }
};
