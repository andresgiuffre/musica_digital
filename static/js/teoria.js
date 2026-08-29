// Traducciones cargadas desde el bloque JSON en base.html (#teoria-i18n-data),
// poblado vía {% trans %}/{% blocktrans %} del lado del servidor -- este archivo es
// estático (no pasa por el motor de templates de Django), así que no puede usar
// esas tags directamente. Fallback a español si por algún motivo el bloque no
// está presente (no debería pasar en uso normal, pero evita un crash duro).
const TeoriaI18n = (() => {
    const el = document.getElementById('teoria-i18n-data');
    if (el) {
        try { return JSON.parse(el.textContent); } catch (e) { console.error('[teoria] Error parseando teoria-i18n-data:', e); }
    }
    return {
        notesMap: { c: 'Do', d: 'Re', e: 'Mi', f: 'Fa', g: 'Sol', a: 'La', b: 'Si' },
        accMap: {}, qualityMap: {}, intervalMap: {}, staffPositions: {},
        fueraDeRangoCentral: 'fuera del rango central',
        note: {}, interval: {}, melody: {}, reading: {}
    };
})();

// Sustituye placeholders {clave} por los valores de `vars` -- equivalente casero al
// %(name)s de gettext/blocktrans del lado Python, ya que JS no trae nada análogo.
function fmt(str, vars) {
    if (!str) return '';
    return str.replace(/\{(\w+)\}/g, (_, k) => (k in vars ? vars[k] : `{${k}}`));
}

const TheoryKB = {
    notesMap: TeoriaI18n.notesMap,
    accMap: TeoriaI18n.accMap,
    qualityMap: TeoriaI18n.qualityMap,
    intervalMap: TeoriaI18n.intervalMap,
    staffPositions: TeoriaI18n.staffPositions,

    getNoteExplanation: function(noteStr, accStr, mode) {
        // noteStr format: 'c/4'
        const T = TeoriaI18n.note;
        const letter = noteStr.charAt(0);
        const name = this.notesMap[letter];
        const position = this.staffPositions[noteStr] || TeoriaI18n.fueraDeRangoCentral;

        // T.ubicacion ya trae el <strong>...</strong> alrededor de {position} en el
        // propio blocktrans -- position va acá como texto plano, sin envolver de nuevo.
        let explanation = fmt(T.ubicacion, { name, position });

        if (accStr === '#') {
            explanation += fmt(T.sostenidoEleva, { name });
        } else if (accStr === 'b') {
            explanation += fmt(T.bemolReduce, { name });
        } else if (accStr === '##') {
            explanation += fmt(T.dobleSostenidoEleva, { name });
        } else if (accStr === 'bb') {
            explanation += fmt(T.dobleBemolReduce, { name });
        } else {
            explanation += T.sinAlteraciones;
        }

        if (mode === 'learning') {
            explanation += `<br><br>${T.observacion}`;
        }

        return explanation;
    },

    getIntervalExplanation: function(degree, quality, mode, isAuditory) {
        const T = TeoriaI18n.interval;

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
        explanation += `<li><strong>${T.distanciaDiatonica}</strong> ${degree}</li>`;
        explanation += `<li><strong>${T.semitonosExactos}</strong> ${totalSemitones}</li>`;
        explanation += `</ul>`;

        // Theory rule
        let rule = "";
        if (quality === 'M') rule = T.reglaMayor;
        if (quality === 'J') rule = T.reglaJusta;
        if (quality === 'm') rule = fmt(T.reglaMenor, { intervalo: this.intervalMap[degree] });
        if (quality === 'A') rule = fmt(T.reglaAumentada, { ref: perfect ? T.refJusta : T.refMayor });
        if (quality === 'd') rule = fmt(T.reglaDisminuida, { ref: perfect ? T.refJusta : T.refMenor });

        explanation += `<p style="text-align: left;">${rule}</p>`;

        if (totalSemitones === 6) {
            explanation += `<p style="text-align: left; color: var(--primary);">${T.tritono}</p>`;
        }

        if (isAuditory) {
            let tip = "";
            if (degree === 5 && quality === 'J') tip = T.tip5J;
            if (degree === 4 && quality === 'J') tip = T.tip4J;
            if (degree === 3 && quality === 'm') tip = T.tip3m;
            if (degree === 3 && quality === 'M') tip = T.tip3M;
            if (degree === 2 && quality === 'm') tip = T.tip2m;
            if (degree === 6 && quality === 'M') tip = T.tip6M;
            if (totalSemitones === 6) tip = T.tipTritono;
            if (degree === 8) tip = T.tip8;

            if (tip) explanation += `<p style="text-align: left; margin-top: 1rem;">${T.consejoAuditivo} ${tip}</p>`;
        }

        if (mode === 'learning') {
            explanation += `<div style="text-align: left; background: rgba(79, 70, 229, 0.05); padding: 1rem; border-radius: 0.5rem; margin-top: 1rem; border-left: 3px solid var(--primary);">${T.observacion}</div>`;
        }

        return explanation;
    },

    getMelodyExplanation: function(correctArr, guessedArr, mode) {
        const T = TeoriaI18n.melody;
        let explanation = "";

        if (correctArr[0] === guessedArr[0] && correctArr[1] === guessedArr[2] && correctArr[2] === guessedArr[1]) {
            explanation = T.intercambio23;
        } else if (correctArr[1] === guessedArr[1] && correctArr[0] === guessedArr[2] && correctArr[2] === guessedArr[0]) {
            explanation = T.intercambio13;
        } else if (correctArr[0] === guessedArr[0] && correctArr[1] === guessedArr[1]) {
            explanation = T.primerasDosOk;
        } else if (correctArr[0] === guessedArr[0]) {
            explanation = T.primeraOk;
        } else {
            explanation = T.formaDiferente;
        }

        let ret = `<p style="text-align: left;"><strong>${T.analisisError}</strong> ${explanation}</p>`;

        if (mode === 'learning') {
            ret += `<div style="text-align: left; background: rgba(79, 70, 229, 0.05); padding: 1rem; border-radius: 0.5rem; margin-top: 1rem; border-left: 3px solid var(--primary);">${T.consejo}</div>`;
        }

        return ret;
    },

    getReadingExplanation: function(timeSignature, totalBeats, mode) {
        const T = TeoriaI18n.reading;
        let numerator = parseInt(timeSignature.split('/')[0]);
        let denominator = parseInt(timeSignature.split('/')[1]);

        let beatsPerMeasure = numerator;
        let beatUnit = denominator === 4 ? T.negras : denominator === 8 ? T.corcheas : T.blancas;

        let explanation = `<ul style="margin-top: 0.5rem; margin-bottom: 1rem; padding-left: 1.5rem; text-align: left;">`;
        explanation += `<li><strong>${T.compas}</strong> ${timeSignature}</li>`;
        explanation += `<li><strong>${T.duracionTotalLabel}</strong> ${fmt(T.duracionTotalValor, { totalBeats })}</li>`;
        explanation += `</ul>`;

        let rule = fmt(T.regla, { timeSignature, beatsPerMeasure, beatUnit });
        explanation += `<p style="text-align: left;">${rule}</p>`;

        if (mode === 'learning') {
            explanation += `<div style="text-align: left; background: rgba(79, 70, 229, 0.05); padding: 1rem; border-radius: 0.5rem; margin-top: 1rem; border-left: 3px solid var(--primary);">${T.observacion}</div>`;
        }

        return explanation;
    },

    // NOTA: no llamado desde ningún template actualmente (trainer_tiempos_fuertes.html
    // arma su propia explicación inline, con su propio i18n) -- se deja sin traducir
    // por ser código muerto, mismo criterio que trainer.html/trainer_solfeo_ritmico.html.
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
