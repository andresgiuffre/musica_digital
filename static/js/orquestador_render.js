/*
 * Componente compartido para renderizar el resultado del Analizador de Partituras
 * (trainer/orquestador_analizar.html y trainer/orquestador_historial_detalle.html
 * lo usan por igual, para no duplicar el diseño de las "cuadros" de presentación).
 */
function escapeHtml(str) {
    if (str === undefined || str === null) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

function renderMapaRegistros(estadisticas) {
    if (!estadisticas) return '';
    const entradas = Object.entries(estadisticas).filter(
        ([, v]) => v.ambito_min_ps !== null && v.ambito_min_ps !== undefined
    );
    if (!entradas.length) return '';

    const globalMin = Math.min(...entradas.map(([, v]) => v.ambito_min_ps));
    const globalMax = Math.max(...entradas.map(([, v]) => v.ambito_max_ps));
    const rango = (globalMax - globalMin) || 1;

    const filas = entradas.map(([nombre, v]) => {
        const left = ((v.ambito_min_ps - globalMin) / rango) * 100;
        const width = Math.max(((v.ambito_max_ps - v.ambito_min_ps) / rango) * 100, 1.5);
        return `
            <div class="registro-fila">
                <span class="registro-label">${escapeHtml(nombre)}</span>
                <div class="registro-track">
                    <div class="registro-barra" style="left: ${left.toFixed(2)}%; width: ${width.toFixed(2)}%;"></div>
                </div>
                <span class="registro-ambito">${escapeHtml(v.ambito)}</span>
            </div>
        `;
    }).join('');

    return `
        <div class="analisis-panel">
            <h3>Mapa de Registros</h3>
            <div class="analisis-mapa-registros">${filas}</div>
        </div>
    `;
}

function renderAlertasViabilidad(alertas) {
    if (!alertas || !alertas.length) return '';
    const filas = alertas.map(a => `
        <div class="analisis-alerta analisis-alerta--${escapeHtml(a.severidad)}">
            <span class="analisis-alerta-icono">${a.severidad === 'excede' ? '⚠️' : '⚡'}</span>
            <span>${escapeHtml(a.mensaje)}</span>
        </div>
    `).join('');
    return `
        <div class="analisis-panel">
            <h3>Alertas de Viabilidad Instrumental</h3>
            <div class="analisis-alertas">${filas}</div>
        </div>
    `;
}

function renderMapaDensidad(densidad) {
    if (!densidad || !densidad.length) return '';
    const bloques = densidad.map(d => {
        const opacidad = d.total_instrumentos > 0 ? (d.instrumentos_activos / d.total_instrumentos) : 0;
        return `<div class="densidad-bloque" style="background-color: color-mix(in srgb, var(--primary) ${(opacidad * 100).toFixed(0)}%, transparent);" title="Compás ${d.compas}: ${d.instrumentos_activos}/${d.total_instrumentos} instrumentos"></div>`;
    }).join('');
    return `
        <div class="analisis-panel">
            <h3>Mapa de Densidad por Compás</h3>
            <div class="densidad-tira-wrap">
                <div class="densidad-tira">${bloques}</div>
            </div>
        </div>
    `;
}

function renderComparacionVersion(comparacion) {
    if (!comparacion || !comparacion.length) return '';
    const iconos = { resuelto: '✅', no_resuelto: '⚠️', sin_verificar: '❔' };
    const filas = comparacion.map(c => `
        <div class="analisis-version-item analisis-version-item--${escapeHtml(c.estado)}">
            <span class="analisis-version-icono">${iconos[c.estado] || '❔'}</span>
            <div>
                <div class="analisis-version-titulo">${escapeHtml(c.parte)} — compases ${escapeHtml(c.compases)} (${escapeHtml(c.accion)})</div>
                <div class="analisis-version-motivo">${escapeHtml(c.motivo)}</div>
            </div>
        </div>
    `).join('');
    return `
        <div class="analisis-panel">
            <h3>Comparación con Versión Anterior</h3>
            <div class="analisis-version-lista">${filas}</div>
        </div>
    `;
}

function _osmdParsePitch(osmdNote) {
    if (!osmdNote.Pitch || osmdNote.Pitch.halfTone === undefined) return null;
    const midi = osmdNote.Pitch.halfTone + 12;
    const notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
    const octave = Math.floor(midi / 12) - 1;
    return notes[midi % 12] + octave;
}

function extraerEventosOsmd(osmd) {
    const eventos = [];
    osmd.cursor.reset();
    while (!osmd.cursor.iterator.EndReached) {
        const ts = osmd.cursor.iterator.currentTimeStamp;
        if (!ts) break;
        const offsetBeats = ts.RealValue * 4;

        const notas = [];
        let duracionBeats = 0;
        const voces = osmd.cursor.iterator.CurrentVoiceEntries;
        if (voces) {
            voces.forEach(v => {
                if (v && v.Notes) {
                    v.Notes.forEach(n => {
                        const pitch = _osmdParsePitch(n);
                        const dur = (n.Length ? n.Length.RealValue : 0) * 4;
                        if (dur > duracionBeats) duracionBeats = dur;
                        if (pitch) notas.push(pitch);
                    });
                }
            });
        }
        if (duracionBeats === 0) duracionBeats = 1; // fallback: negra

        if (notas.length) {
            eventos.push({ notes: notas, offset: offsetBeats, duration: duracionBeats });
        }
        osmd.cursor.next();
    }
    osmd.cursor.reset();
    return eventos;
}

function agregarBotonPlay(columna, osmd, bpm, instrumento) {
    if (!columna || columna.querySelector('.analisis-btn-play')) return;
    const contenedorOsmd = columna.querySelector('div');
    const boton = document.createElement('button');
    boton.type = 'button';
    boton.className = 'analisis-btn-play';
    boton.textContent = '▶ Reproducir';
    boton.addEventListener('click', () => {
        const eventos = extraerEventosOsmd(osmd);
        AudioEngine.playSequence(eventos, bpm, instrumento);
    });
    columna.insertBefore(boton, contenedorOsmd);
}

function renderEdicionesSugeridas(ediciones, analysisId) {
    if (!ediciones || !ediciones.length) return '';
    let contador = 0;
    const filas = ediciones.map(e => {
        const filaPrincipal = `
            <tr>
                <td>${escapeHtml(e.compases)}</td>
                <td>${escapeHtml(e.parte)}</td>
                <td>${escapeHtml(e.accion)}</td>
                <td>${escapeHtml(e.detalle)}</td>
            </tr>
        `;

        // Solo transponer_octava tiene pentagrama comparado: comparar contra compases de
        // silencio (silenciar) no aporta nada visualmente, queda solo como texto.
        const esEjecutable = analysisId && e.accion_tipo === 'transponer_octava';
        if (!esEjecutable) return filaPrincipal;

        contador += 1;
        const targetId = `comparacion-${analysisId}-${contador}-${Math.random().toString(36).slice(2, 7)}`;
        const filaComparacion = `
            <tr class="analisis-edicion-comparacion-fila">
                <td colspan="4">
                    <button type="button" class="analisis-btn-comparar"
                        data-target="${targetId}"
                        data-analysis-id="${analysisId}"
                        data-parte="${escapeHtml(e.parte)}"
                        data-compas-desde="${e.compas_desde}"
                        data-compas-hasta="${e.compas_hasta}"
                        data-accion-tipo="${escapeHtml(e.accion_tipo)}"
                        data-direccion="${escapeHtml(e.direccion || '')}">Ver pentagramas comparados</button>
                    <div class="analisis-comparacion" id="${targetId}" style="display:none;">
                        <div class="analisis-comparacion-columna">
                            <span class="analisis-sub-label">Original</span>
                            <span class="analisis-comparacion-rango"></span>
                            <div class="analisis-osmd-original"></div>
                        </div>
                        <div class="analisis-comparacion-columna">
                            <span class="analisis-sub-label">Editado</span>
                            <span class="analisis-comparacion-rango"></span>
                            <div class="analisis-osmd-editado"></div>
                        </div>
                    </div>
                </td>
            </tr>
        `;
        return filaPrincipal + filaComparacion;
    }).join('');

    return `
        <div class="analisis-ediciones">
            <span class="analisis-sub-label">Ediciones sugeridas</span>
            <div class="analisis-tabla-wrap">
                <table class="analisis-tabla analisis-tabla--mini">
                    <thead><tr><th>Compás</th><th>Parte</th><th>Acción</th><th>Detalle</th></tr></thead>
                    <tbody>${filas}</tbody>
                </table>
            </div>
        </div>
    `;
}

function cargarFragmentoComparado(btn, contenedor) {
    const originalCol = contenedor.querySelectorAll('.analisis-comparacion-columna')[0];
    const editadoCol = contenedor.querySelectorAll('.analisis-comparacion-columna')[1];
    const originalDiv = contenedor.querySelector('.analisis-osmd-original');
    const editadoDiv = contenedor.querySelector('.analisis-osmd-editado');
    originalDiv.textContent = 'Cargando...';
    editadoDiv.textContent = '';

    const params = new URLSearchParams({
        parte: btn.dataset.parte,
        compas_desde: btn.dataset.compasDesde,
        compas_hasta: btn.dataset.compasHasta,
        accion_tipo: btn.dataset.accionTipo,
        direccion: btn.dataset.direccion,
    });

    fetch(`/api/orquestador/fragmento/${btn.dataset.analysisId}/?${params.toString()}`)
        .then(res => res.json())
        .then(data => {
            if (data.status !== 'success') {
                originalDiv.textContent = 'Error: ' + data.message;
                editadoDiv.textContent = '';
                return;
            }
            originalDiv.textContent = '';
            editadoDiv.textContent = '';

            if (typeof data.rango_mostrado_desde === 'number' && typeof data.rango_mostrado_hasta === 'number') {
                const textoRango = `Compases ${data.rango_mostrado_desde}-${data.rango_mostrado_hasta} (edición: ${btn.dataset.compasDesde}-${btn.dataset.compasHasta})`;
                originalCol.querySelector('.analisis-comparacion-rango').textContent = textoRango;
                editadoCol.querySelector('.analisis-comparacion-rango').textContent = textoRango;
            }

            const osmdOriginal = new opensheetmusicdisplay.OpenSheetMusicDisplay(originalDiv, { autoResize: true, drawTitle: false });
            const osmdEditado = new opensheetmusicdisplay.OpenSheetMusicDisplay(editadoDiv, { autoResize: true, drawTitle: false });
            const bpm = data.tempo_bpm || 100;

            Promise.all([
                osmdOriginal.load(data.original_musicxml).then(() => osmdOriginal.render()),
                osmdEditado.load(data.editado_musicxml).then(() => osmdEditado.render()),
            ]).then(() => {
                agregarBotonPlay(originalCol, osmdOriginal, bpm, btn.dataset.parte);
                agregarBotonPlay(editadoCol, osmdEditado, bpm, btn.dataset.parte);
            });
        })
        .catch(err => {
            originalDiv.textContent = 'Error de red al cargar el fragmento.';
            editadoDiv.textContent = '';
            console.error(err);
        });
}

// Referencia a la comparacion actualmente visible (a lo sumo una por vez). OpenSheetMusicDisplay
// 1.8.x tiene EngravingRules como singleton global compartido entre instancias (ver
// https://github.com/opensheetmusicdisplay/opensheetmusicdisplay/issues/559) — mantener más de
// una instancia viva al mismo tiempo en la página es lo que puede hacer que una tarjeta termine
// mostrando el contenido de otra. Cerramos siempre la anterior antes de abrir una nueva.
let comparacionAbierta = null;

function cerrarComparacion(contenedor) {
    contenedor.style.display = 'none';
    contenedor.querySelector('.analisis-osmd-original').innerHTML = '';
    contenedor.querySelector('.analisis-osmd-editado').innerHTML = '';
    contenedor.querySelectorAll('.analisis-comparacion-rango').forEach(el => { el.textContent = ''; });
    delete contenedor.dataset.cargado;
    if (comparacionAbierta === contenedor) comparacionAbierta = null;
}

function handleComparacionClick(event) {
    const btn = event.target.closest('.analisis-btn-comparar');
    if (!btn) return;
    const contenedor = document.getElementById(btn.dataset.target);
    if (!contenedor) return;

    if (contenedor.style.display !== 'none') {
        cerrarComparacion(contenedor);
        return;
    }

    if (comparacionAbierta && comparacionAbierta !== contenedor) {
        cerrarComparacion(comparacionAbierta);
    }

    contenedor.style.display = 'flex';
    comparacionAbierta = contenedor;
    cargarFragmentoComparado(btn, contenedor);
}

function renderErrorFallback(data, container) {
    const panel = document.createElement('div');
    panel.className = 'analisis-panel analisis-panel--error';
    panel.innerHTML = `
        <h3>No se pudo generar el análisis con IA</h3>
        <p class="analisis-prosa">${escapeHtml(data.error)}</p>
        ${data.raw_music_data ? '<details class="analisis-raw-details"><summary>Ver datos musicales extraídos</summary><pre></pre></details>' : ''}
    `;
    if (data.raw_music_data) {
        panel.querySelector('pre').textContent = JSON.stringify(data.raw_music_data, null, 2);
    }
    container.appendChild(panel);
}

function renderAnalysisResult(data, container, analysisId) {
    container.innerHTML = '';

    if (!data || data.error) {
        renderErrorFallback(data || { error: 'Sin datos.' }, container);
        return;
    }

    if (analysisId) {
        const descargaWrap = document.createElement('div');
        descargaWrap.className = 'analisis-descarga';
        descargaWrap.innerHTML = `<a href="/orquestador/exportar/${analysisId}/" class="analisis-btn-pdf" target="_blank" rel="noopener">⬇ Descargar PDF</a>`;
        container.appendChild(descargaWrap);
    }

    const resumenPanel = document.createElement('div');
    resumenPanel.className = 'analisis-panel';
    resumenPanel.innerHTML = `
        <h3>Resumen General</h3>
        <p class="analisis-prosa">${escapeHtml(data.resumen_general)}</p>
    `;
    container.appendChild(resumenPanel);

    if (data.resumen_por_instrumento && data.resumen_por_instrumento.length) {
        const filas = data.resumen_por_instrumento.map(r => `
            <tr>
                <td class="analisis-td-instrumento">${escapeHtml(r.instrumento)}</td>
                <td>${escapeHtml(r.descripcion)}</td>
            </tr>
        `).join('');
        const tablaPanel = document.createElement('div');
        tablaPanel.className = 'analisis-panel';
        tablaPanel.innerHTML = `
            <h3>Resumen por Instrumento</h3>
            <div class="analisis-tabla-wrap">
                <table class="analisis-tabla">
                    <thead><tr><th>Instrumento</th><th>Análisis</th></tr></thead>
                    <tbody>${filas}</tbody>
                </table>
            </div>
        `;
        container.appendChild(tablaPanel);
    }

    const mapaHtml = renderMapaRegistros(data.estadisticas_por_instrumento);
    if (mapaHtml) {
        const mapaWrap = document.createElement('div');
        mapaWrap.innerHTML = mapaHtml.trim();
        container.appendChild(mapaWrap.firstElementChild);
    }

    const densidadHtml = renderMapaDensidad(data.densidad_por_compas);
    if (densidadHtml) {
        const densidadWrap = document.createElement('div');
        densidadWrap.innerHTML = densidadHtml.trim();
        container.appendChild(densidadWrap.firstElementChild);
    }

    const alertasHtml = renderAlertasViabilidad(data.alertas_viabilidad);
    if (alertasHtml) {
        const alertasWrap = document.createElement('div');
        alertasWrap.innerHTML = alertasHtml.trim();
        container.appendChild(alertasWrap.firstElementChild);
    }

    const comparacionHtml = renderComparacionVersion(data.comparacion_version_anterior);
    if (comparacionHtml) {
        const comparacionWrap = document.createElement('div');
        comparacionWrap.innerHTML = comparacionHtml.trim();
        container.appendChild(comparacionWrap.firstElementChild);
    }

    if (data.bloques && data.bloques.length) {
        const bloquesWrap = document.createElement('div');
        bloquesWrap.className = 'analisis-bloques';
        data.bloques.forEach((bloque, idx) => {
            const details = document.createElement('details');
            details.className = 'analisis-bloque';
            if (idx === 0) details.open = true;
            details.innerHTML = `
                <summary class="analisis-bloque-summary">Compases ${escapeHtml(bloque.rango_compases)}</summary>
                <div class="analisis-bloque-body">
                    <div class="analisis-bloque-grid">
                        <div class="analisis-sub analisis-sub--cuerdas">
                            <span class="analisis-sub-label">Cuerdas</span>
                            <p>${escapeHtml(bloque.analisis_cuerdas)}</p>
                        </div>
                        <div class="analisis-sub analisis-sub--maderas">
                            <span class="analisis-sub-label">Maderas</span>
                            <p>${escapeHtml(bloque.analisis_maderas)}</p>
                        </div>
                        <div class="analisis-sub analisis-sub--metales">
                            <span class="analisis-sub-label">Metales / Percusión</span>
                            <p>${escapeHtml(bloque.analisis_metales_percusion)}</p>
                        </div>
                        <div class="analisis-sub analisis-sub--balance">
                            <span class="analisis-sub-label">Balance y Fango</span>
                            <p>${escapeHtml(bloque.analisis_balance_y_fango)}</p>
                        </div>
                    </div>
                    <div class="analisis-solucion">
                        <span class="analisis-sub-label">Solución</span>
                        <p>${escapeHtml(bloque.solucion_prosa)}</p>
                    </div>
                    ${renderEdicionesSugeridas(bloque.ediciones_sugeridas, analysisId)}
                </div>
            `;
            bloquesWrap.appendChild(details);
        });
        container.appendChild(bloquesWrap);
    }

    if (!container.dataset.comparacionBound) {
        container.addEventListener('click', handleComparacionClick);
        container.dataset.comparacionBound = '1';
    }
}

window.renderAnalysisResult = renderAnalysisResult;
