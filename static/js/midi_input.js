/*
 * Wrapper delgado sobre Web MIDI API. Extraído de la lógica duplicada casi byte a byte
 * entre midi_hub.html y midi_game_chords.html (requestMIDIAccess, enumeración de inputs,
 * parseo de mensajes note-on/note-off, reconexión en caliente vía onstatechange). No toca
 * DOM ni persiste nada por defecto -- quien llama arma su propia UI (select de
 * dispositivo, etc.) a partir de los callbacks. midi_hub.html y midi_game_chords.html
 * todavía NO fueron migrados a este módulo (quedan con su propia copia); ver el plan de
 * la sesión que lo introdujo para la justificación.
 */
window.MidiInput = (function () {

    /*
     * opciones:
     *   onDispositivosDisponibles(lista) -- lista=[{id, name}], se llama al conectar y de
     *     nuevo cada vez que cambia el set de dispositivos (enchufar/desenchufar). Lista
     *     vacía === no hay ningún teclado disponible ahora mismo.
     *   onSinSoporte() -- el navegador no implementa Web MIDI API.
     *   onPermisoDenegado() -- requestMIDIAccess() fue rechazada.
     *   onNoteOn(pitch, velocity) / onNoteOff(pitch) -- despachados desde el input
     *     actualmente seleccionado (ver seleccionarDispositivo).
     *   nombreClaveStorage (opcional) -- si se pasa, la selección de dispositivo se
     *     persiste en localStorage bajo esa clave y se restaura en la próxima conexión
     *     (mismo criterio que ya usan midi_hub.html/midi_game_chords.html, clave
     *     'selectedMidiInputId' -- acá queda opt-in y con nombre propio por consumidor).
     *
     * Devuelve un handle: { obtenerDispositivos(), seleccionarDispositivo(id), desconectar() }
     */
    function conectar(opciones) {
        const cb = opciones || {};
        let midiAccess = null;
        let dispositivos = []; // [{id, name}]
        let inputActual = null;
        let idSeleccionado = null;

        function despacharNota(event) {
            const status = event.data[0];
            const pitch = event.data[1] || 0;
            const velocity = (event.data.length > 2) ? event.data[2] : 0;
            const cmd = status >> 4; // 4 bits más significativos; el canal (bits bajos) no se distingue

            if (cmd !== 9 && cmd !== 8) return; // solo note-on/note-off, se ignora el resto

            try {
                if (cmd === 9 && velocity > 0) {
                    if (cb.onNoteOn) cb.onNoteOn(pitch, velocity);
                } else if (cmd === 8 || (cmd === 9 && velocity === 0)) {
                    // velocity 0 en un note-on es, por convención MIDI, un note-off
                    if (cb.onNoteOff) cb.onNoteOff(pitch);
                }
            } catch (e) {
                console.error('[MidiInput] Error procesando mensaje MIDI:', e);
            }
        }

        function actualizarListaDispositivos() {
            const inputs = Array.from(midiAccess.inputs.values());
            dispositivos = inputs.map(i => ({ id: i.id, name: i.name }));

            // Se limpia cualquier listener viejo antes de reasignar, mismo criterio que el
            // patrón existente en midi_hub.html/midi_game_chords.html.
            inputs.forEach(i => { i.onmidimessage = null; });

            if (inputs.length === 0) {
                inputActual = null;
                idSeleccionado = null;
                if (cb.onDispositivosDisponibles) cb.onDispositivosDisponibles([]);
                return;
            }

            let candidatoId = idSeleccionado;
            if (!candidatoId && cb.nombreClaveStorage) {
                candidatoId = localStorage.getItem(cb.nombreClaveStorage);
            }
            if (!candidatoId || !inputs.some(i => i.id === candidatoId)) {
                candidatoId = inputs[0].id;
            }

            seleccionarDispositivoInterno(candidatoId, inputs);
            if (cb.onDispositivosDisponibles) cb.onDispositivosDisponibles(dispositivos);
        }

        function seleccionarDispositivoInterno(id, inputsConocidos) {
            const inputs = inputsConocidos || Array.from(midiAccess.inputs.values());
            const input = inputs.find(i => i.id === id);
            if (!input) return;

            if (inputActual) inputActual.onmidimessage = null;
            inputActual = input;
            idSeleccionado = id;
            inputActual.onmidimessage = despacharNota;

            if (cb.nombreClaveStorage) {
                localStorage.setItem(cb.nombreClaveStorage, id);
            }
        }

        if (!navigator.requestMIDIAccess) {
            if (cb.onSinSoporte) cb.onSinSoporte();
            return { obtenerDispositivos: () => [], seleccionarDispositivo: () => {}, desconectar: () => {} };
        }

        navigator.requestMIDIAccess().then(
            (access) => {
                midiAccess = access;
                actualizarListaDispositivos();
                midiAccess.onstatechange = actualizarListaDispositivos;
            },
            () => {
                if (cb.onPermisoDenegado) cb.onPermisoDenegado();
            }
        );

        return {
            obtenerDispositivos: () => dispositivos,
            seleccionarDispositivo: (id) => seleccionarDispositivoInterno(id),
            desconectar: () => {
                if (midiAccess) midiAccess.onstatechange = null;
                if (inputActual) inputActual.onmidimessage = null;
                inputActual = null;
            }
        };
    }

    return { conectar: conectar };
})();
