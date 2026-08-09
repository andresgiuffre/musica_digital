/*
 * Utilidades compartidas para trabajar con una partitura cargada en OSMD. Dos consumidores
 * hoy: biblioteca_play.html (arma la secuencia de reproducción/práctica agrupando por
 * acorde) y orquestacion_ejercicio.html, que usa etiquetarNotasConSvg pero mantiene su
 * propia recolectarNotasOsmd (resuelve un problema distinto: correlacionar notas contra un
 * endpoint backend separado, no solo recorrer el cursor -- ver el plan de la sesión que
 * introdujo este archivo).
 */
window.OsmdUtils = (function () {

    const NOMBRES_NOTA = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

    function pitchANombre(midi) {
        const octava = Math.floor(midi / 12) - 1;
        const nombre = NOMBRES_NOTA[midi % 12];
        return nombre + octava;
    }

    /*
     * Recorre osmd.cursor de punta a punta (reset -> next() -> ... -> EndReached) y junta
     * dos listas:
     *   - pasos: uno por CADA parada del cursor, sin excepción (incluye silencios sin
     *     ninguna nota) -- necesario para que un índice contra esta lista corresponda
     *     siempre a la misma cantidad de cursor.next(), incluso en compases sin notas.
     *   - notas: una entrada por cada nota real encontrada (puede haber varias por parada,
     *     si hay acorde o varias voces), ligada a su paso vía groupIndex.
     * ps/pitchName quedan en null si la nota no tiene Pitch resuelto (ej. percusión sin
     * altura) -- se sigue registrando por su duración (afecta a quien calcule cuánto dura
     * el paso) pero no aporta una nota audible.
     */
    function recorrerCursorOsmd(osmd, opciones) {
        const incluirGraceNotes = !opciones || opciones.incluirGraceNotes !== false;
        const pasos = [];
        const notas = [];

        osmd.cursor.reset();
        let groupIndex = 0;
        while (!osmd.cursor.iterator.EndReached) {
            const ts = osmd.cursor.iterator.currentTimeStamp;
            if (!ts) break;

            const offsetBeats = ts.RealValue * 4;
            const measureIndex = osmd.cursor.iterator.CurrentMeasureIndex;
            pasos.push({ groupIndex, offsetBeats, measureIndex });

            const voces = osmd.cursor.iterator.CurrentVoiceEntries;
            if (voces) {
                voces.forEach(v => {
                    if (v && v.Notes) {
                        v.Notes.forEach(n => {
                            const esGrace = !!n.IsGraceNote;
                            if (esGrace && !incluirGraceNotes) return;

                            const tienePitch = n.Pitch && n.Pitch.halfTone !== undefined;
                            const ps = tienePitch ? (n.Pitch.halfTone + 12) : null;

                            notas.push({
                                groupIndex,
                                offsetBeats,
                                measureIndex,
                                ps: ps,
                                pitchName: tienePitch ? pitchANombre(ps) : null,
                                durationRealValue: n.Length ? n.Length.RealValue : 0,
                                isGrace: esGrace,
                                logicalNote: n
                            });
                        });
                    }
                });
            }

            osmd.cursor.next();
            groupIndex++;
        }
        osmd.cursor.reset();

        return { pasos, notas };
    }

    /*
     * Lleva el cursor visible de OSMD desde currentIdx hasta idx, sea hacia adelante
     * (cursor.next() repetido) o hacia atrás (reset() + reconstruir desde 0) -- mismo
     * patrón que ya usaban por separado el loop principal de reproducción y el salto al
     * inicio de un loop por compases en biblioteca_play.html. Devuelve el nuevo índice
     * (no muta nada por referencia: JS no tiene forma limpia de mutar un primitivo del
     * llamador, así que el llamador reasigna su propia variable con el valor devuelto).
     */
    function avanzarCursorOsmdHasta(osmd, currentIdx, idx) {
        if (idx < currentIdx) {
            osmd.cursor.reset();
            currentIdx = 0;
        }
        while (currentIdx < idx) {
            osmd.cursor.next();
            currentIdx++;
        }
        return currentIdx;
    }

    /*
     * Recorre osmd.GraphicSheet.MeasureList (el árbol YA renderizado) y le agrega .svgEl a
     * cada entrada de `notas` (cualquier lista plana con .logicalNote -- la que devuelve
     * recorrerCursorOsmd sirve tal cual) buscando su elemento SVG real. Muta in-place y no
     * devuelve nada -- las entradas que no encuentran su SVG simplemente no reciben .svgEl.
     * Promovido desde orquestacion_ejercicio.html (etiquetarNotas), con una diferencia real:
     * usa un Map<logicalNote, entry> en vez de notas.find() dentro del recorrido, O(n) en
     * vez de O(n²) sobre notas × elementos gráficos.
     *
     * Hay que volver a llamarla después de CADA osmd.render(): un render reconstruye el SVG
     * entero y cualquier .svgEl guardado antes queda huérfano (apunta a un nodo ya
     * desconectado del documento).
     */
    function etiquetarNotasConSvg(osmd, notas) {
        const porLogicalNote = new Map();
        notas.forEach(n => { if (n.logicalNote) porLogicalNote.set(n.logicalNote, n); });

        try {
            osmd.GraphicSheet.MeasureList.forEach(sistema => {
                sistema.forEach(medida => {
                    if (!medida || !medida.staffEntries) return;
                    medida.staffEntries.forEach(staffEntry => {
                        (staffEntry.graphicalVoiceEntries || []).forEach(gve => {
                            (gve.notes || []).forEach(gNota => {
                                const entry = porLogicalNote.get(gNota.sourceNote);
                                if (!entry) return;

                                let svgEl = null;
                                if (typeof gNota.getSVGGElement === 'function') svgEl = gNota.getSVGGElement();
                                else if (gNota.vfnote && gNota.vfnote[0] && gNota.vfnote[0].attrs) svgEl = gNota.vfnote[0].attrs.el;
                                if (svgEl) entry.svgEl = svgEl;
                            });
                        });
                    });
                });
            });
        } catch (e) {
            console.error('[OsmdUtils] Error recorriendo GraphicSheet:', e);
        }
    }

    return {
        recorrerCursorOsmd: recorrerCursorOsmd,
        avanzarCursorOsmdHasta: avanzarCursorOsmdHasta,
        etiquetarNotasConSvg: etiquetarNotasConSvg,
        pitchANombre: pitchANombre
    };
})();
