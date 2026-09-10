/*
 * Utilidades compartidas de INTERACCIÓN para pantallas de ejercicio -- selección de
 * respuesta, feedback de acierto/error, envío del intento al backend, y (para las que
 * usan OSMD) selección de notas sobre la partitura renderizada. No es sobre teoría
 * musical ni audio (eso ya lo resuelven teoria.js/audio_engine.js) ni sobre extraer datos
 * de OSMD (eso lo resuelve osmd_utils.js, que este archivo consume pero no reemplaza).
 *
 * Nace de una auditoría real sobre los ~10 juegos de trainer_*.html y los ejercicios de
 * Cursos (tema_detail.html): el mismo bloque "fetch record_attempt -> actualizar streak/
 * nivel/XP" está casi idéntico en 9 templates, y la selección Shift=rango/Ctrl=individual
 * sobre notas de OSMD se había reimplementado de cero dos veces (orquestacion_libre_
 * ejercicio.html y el ejercicio de Ligaduras y Puntillo) sin que nada lo documentara.
 *
 * Como osmd_utils.js, se carga con <script> template a template (no globalmente en
 * base.html) -- no todas las páginas con ejercicios usan OSMD, y ninguna página sin
 * ejercicios (dashboard, perfil) lo necesita.
 *
 * Retrofit deliberadamente NO retroactivo: los ejercicios que ya funcionan (los 9 juegos
 * de trainer_*.html, los 5 tipos de Cursos ya implementados) se migran de forma
 * oportunista cuando se toquen por otra razón, no en una pasada dedicada. El primer
 * consumidor real es el próximo ejercicio de Grado 2.
 */
window.EjercicioUI = (function () {

    // --- Sección A: registrar un intento contra record_attempt + actualizar el HUD ---

    /*
     * Envuelve el POST a record_attempt (los 9 juegos de trainer_*.html lo hacen con la
     * MISMA forma de payload/respuesta: {status, current_streak, level_info}) y la
     * actualización del HUD de progreso que sigue. `refs` son elementos DOM ya resueltos
     * por el caller (no ids -- cada juego nombra los suyos distinto en algún caso) y son
     * todos opcionales: un caller puede pasar solo los que tenga en su página. Devuelve la
     * Promise ya resuelta a `data`, para que el caller siga encadenando lo que le haga
     * falta (mostrar el modal de teoría, dibujar la siguiente pregunta, etc.).
     */
    function registrarIntentoJuego(url, payload, csrfToken, refs) {
        refs = refs || {};
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify(payload)
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data.status === 'success') {
                    if (refs.streakCounter) refs.streakCounter.innerText = data.current_streak;
                    if (data.level_info) {
                        if (refs.uiLevel) refs.uiLevel.innerText = data.level_info.level;
                        if (refs.uiXp) refs.uiXp.innerText = data.level_info.xp_in_level;
                        if (refs.uiXpNeeded) refs.uiXpNeeded.innerText = data.level_info.xp_needed;
                        if (refs.uiProgressBar) refs.uiProgressBar.style.width = data.level_info.progress_percentage + '%';
                    }
                }
                return data;
            });
    }

    /*
     * Hermano de registrarIntentoJuego para los ejercicios de Cursos (Práctica dirigida,
     * Ligaduras y Puntillo, y los demás tipos de bloque de tema_detail.html): la forma de
     * respuesta del backend es distinta (`{status, mejor_precision, completado,
     * veces_practicado}`, no `{status, current_streak, level_info}`), así que no comparte
     * código con registrarIntentoJuego más allá de la idea general. Solo se ocupa del POST
     * y de actualizar el badge de "completado/en progreso" -- el resto (ocultar la UI del
     * propio ejercicio, mostrar el resumen, decidir cuándo recargar la página) varía entre
     * tipos de bloque en timing y en qué contenedores tocan, así que se deja en manos del
     * caller: la Promise resuelve a `resultado` para que siga encadenando lo que le haga
     * falta, mismo criterio que registrarIntentoJuego.
     *
     * `refs.txtCompletado`/`refs.txtNoCompletado` son funciones (resultado => string), no
     * strings fijos -- cada tipo de bloque arma su propio texto localizado con su propio
     * `fmt()`/`data.txt*` (interpolando `mejor_precision` o `veces_practicado`), y este
     * módulo no tiene forma de saber cuál corresponde a cuál.
     */
    function registrarResultadoBloque(url, payload, csrfToken, refs) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify(payload)
        })
            .then(function (res) { return res.json(); })
            .then(function (resultado) {
                if (resultado.status === 'success' && refs && refs.divBadge) {
                    refs.divBadge.classList.remove(refs.claseBadgeCompletado, refs.claseBadgeProgreso);
                    if (resultado.completado) {
                        refs.divBadge.classList.add(refs.claseBadgeCompletado);
                        refs.divBadge.textContent = refs.txtCompletado(resultado);
                    } else {
                        refs.divBadge.classList.add(refs.claseBadgeProgreso);
                        refs.divBadge.textContent = refs.txtNoCompletado(resultado);
                    }
                    refs.divBadge.style.display = '';
                }
                return resultado;
            });
    }

    // --- Sección B: feedback visual de acierto/error ---

    /*
     * Único comportamiento del módulo: clase CSS (flash-correct/flash-incorrect,
     * definidas en base.html y sobreescritas en gabinete.css), retirada sola después de
     * `duracionMs` (500 por defecto, la misma duración que ya usan los juegos que hoy
     * implementan este patrón). Se decidió a propósito NO ofrecer el estilo alternativo de
     * `style.borderColor` manual que usan otros 5 juegos hoy (trainer_acordes/tiempos_
     * fuertes/sincopas/progresiones/dictado_melodico) como una opción más del módulo --
     * eso institucionalizaría la inconsistencia que se quiere dejar atrás. Esos 5 archivos
     * quedan con su código actual tal cual, migran a destellar() de forma oportunista.
     */
    function destellar(elemento, esCorrecta, opciones) {
        if (!elemento) return;
        const duracionMs = (opciones && opciones.duracionMs) || 500;
        elemento.classList.add(esCorrecta ? 'flash-correct' : 'flash-incorrect');
        setTimeout(function () {
            elemento.classList.remove('flash-correct', 'flash-incorrect');
        }, duracionMs);
    }

    // --- Sección C: interacción con notas sobre un SVG de OSMD ---

    /*
     * Marca cada nota con .svgEl (data-osmd-nota + clase nota-clickeable, mismo par que ya
     * usaban orquestacion_ejercicio.html/orquestacion_libre_ejercicio.html/tema_detail.html
     * por separado) y arma un Map<svgEl, entry[]> para resolver un click delegado
     * (`ev.target.closest('[data-osmd-nota]')` -> `mapa.get(el)`). No filtra la lista de
     * entrada -- el caller decide qué notas son seleccionables (ej. excluir silencios,
     * excluir grace notes) antes de llamar, pasando ya el array que le interesa. Un mismo
     * svgEl puede mapear a más de una entrada (acordes, o la misma nota impresa tocada más
     * de una vez por una repetición) -- mismo motivo que ya documentó osmd_utils.js para
     * etiquetarNotasConSvg.
     */
    function etiquetarNotasClickeables(notas) {
        const mapa = new Map();
        notas.forEach(function (n) {
            if (!n.svgEl) return;
            n.svgEl.setAttribute('data-osmd-nota', '1');
            n.svgEl.classList.add('nota-clickeable');
            if (!mapa.has(n.svgEl)) mapa.set(n.svgEl, []);
            mapa.get(n.svgEl).push(n);
        });
        return mapa;
    }

    /*
     * Shift = rango desde el ancla hasta la nota clickeada, incluyendo las intermedias
     * (mismo comportamiento estándar de cualquier lista/explorador de archivos). Ctrl/Cmd =
     * agregar o sacar una nota puntual sin afectar el resto. Click simple = reemplaza toda
     * la selección por esta sola nota, salvo que ya fuera la única seleccionada, en cuyo
     * caso la deselecciona. `notasOrdenadas` ya viene filtrado/agrupado por el caller (ej.
     * por "parte" en un ejercicio multi-instrumento, o el array entero en uno monofónico) --
     * esta función no asume nada sobre esa lógica de dominio, solo trabaja sobre índices
     * dentro del array que se le pasa. Devuelve la selección y el ancla NUEVAS -- no muta
     * nada por referencia, mismo motivo que avanzarCursorOsmdHasta en osmd_utils.js.
     */
    function alternarSeleccion(params) {
        const seleccionActual = params.seleccionActual;
        const notaAncla = params.notaAncla;
        const notasOrdenadas = params.notasOrdenadas;
        const notaClickeada = params.notaClickeada;
        const shiftKey = params.shiftKey;
        const ctrlKey = params.ctrlKey;

        if (shiftKey && notaAncla != null) {
            const idxAncla = notasOrdenadas.indexOf(notaAncla);
            const idxClickeada = notasOrdenadas.indexOf(notaClickeada);
            if (idxAncla !== -1 && idxClickeada !== -1) {
                const desde = Math.min(idxAncla, idxClickeada);
                const hasta = Math.max(idxAncla, idxClickeada);
                return { seleccion: notasOrdenadas.slice(desde, hasta + 1), ancla: notaAncla };
            }
        }

        if (ctrlKey) {
            const yaEstaba = seleccionActual.indexOf(notaClickeada) !== -1;
            const seleccion = yaEstaba
                ? seleccionActual.filter(function (n) { return n !== notaClickeada; })
                : seleccionActual.concat([notaClickeada]);
            return { seleccion: seleccion, ancla: notaClickeada };
        }

        if (seleccionActual.length === 1 && seleccionActual[0] === notaClickeada) {
            return { seleccion: [], ancla: null };
        }
        return { seleccion: [notaClickeada], ancla: notaClickeada };
    }

    /*
     * Glow de una nota individual dentro de OSMD vía style.filter -- "el mecanismo
     * confirmado" (drop-shadow doble, no solo cambiar fill/stroke: VexFlow puede fijar el
     * fill con más especificidad en un hijo y taparlo), documentado primero en
     * biblioteca_play.html (pintarNotaAcierto/flashNotasError) y reimplementado igual al
     * menos 2 veces más en tema_detail.html.
     */
    function pintarNotaSvg(svgEl, color) {
        if (!svgEl) return;
        svgEl.style.filter = 'drop-shadow(0 0 3px ' + color + ') drop-shadow(0 0 7px ' + color + ')';
        svgEl.style.fill = color;
    }

    function limpiarNotaSvg(svgEl) {
        if (!svgEl) return;
        svgEl.style.filter = '';
        svgEl.style.fill = '';
    }

    return {
        registrarIntentoJuego: registrarIntentoJuego,
        registrarResultadoBloque: registrarResultadoBloque,
        destellar: destellar,
        etiquetarNotasClickeables: etiquetarNotasClickeables,
        alternarSeleccion: alternarSeleccion,
        pintarNotaSvg: pintarNotaSvg,
        limpiarNotaSvg: limpiarNotaSvg
    };
})();
