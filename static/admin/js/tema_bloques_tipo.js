// Filtra, dentro del admin de Tema, tanto las opciones del desplegable "tipo"
// de cada bloque de contenido como los fieldsets colapsables que muestran sus
// campos especificos (Texto/Ejemplo de partitura/Imagen/Video vs. [G0]
// Practica dirigida/Ritmo matematico/Completar el compas/Ubicacion de lineas
// y espacios/[G1] Ligaduras y Puntillo) -- segun el "Tipo de Tema"
// (Lectura/Practica) elegido en #id_tipo. Objetivo: que la lista de tipos de
// actividad no siga creciendo sin limite a medida que se suman ejercicios
// nuevos.
//
// Espejo a mano de BloqueContenido.TIPO_CHOICES/TIPOS_LECTURA/TIPOS_PRACTICA
// en models.py -- no se puede leer eso directamente desde aca (Python no
// corre en el navegador), asi que un tipo de bloque nuevo se agrega en AMBOS
// lugares. Los titulos de fieldset de abajo tienen que ser el texto EXACTO
// del fieldset correspondiente en trainer/admin.py (BloqueContenidoInline).
(function () {
    'use strict';

    // value -> nombre del fieldset que lo edita. PRACTICA no tiene entrada:
    // ya no tiene fieldset propio en el admin (confirmado que no se usa).
    var FIELDSET_DE_TIPO = {
        TEXTO: 'Texto',
        EJEMPLO_PARTITURA: 'Ejemplo de partitura',
        IMAGEN: 'Imagen',
        VIDEO: 'Video',
        PRACTICA_DIRIGIDA: '[G0] Práctica dirigida',
        RITMO_MATEMATICA: '[G0] Ritmo matemático',
        COMPLETAR_COMPAS: '[G0] Completar el compás',
        LINEAS_ESPACIOS: '[G0] Ubicación de líneas y espacios',
        LIGADURAS_PUNTILLO: '[G1] Ligaduras y Puntillo',
    };
    var TIPOS_LECTURA = ['TEXTO', 'EJEMPLO_PARTITURA', 'IMAGEN', 'VIDEO'];
    var TIPOS_PRACTICA = ['PRACTICA_DIRIGIDA', 'RITMO_MATEMATICA', 'COMPLETAR_COMPAS', 'LINEAS_ESPACIOS', 'LIGADURAS_PUNTILLO'];

    function tiposPermitidos(tipoTema) {
        return tipoTema === 'PRACTICA' ? TIPOS_PRACTICA : TIPOS_LECTURA;
    }

    function filtrarSelectTipo(select, permitidos) {
        var valorActual = select.value;
        Array.prototype.forEach.call(select.options, function (opt) {
            if (!opt.value) return; // deja la opcion vacia ("---------") como esta
            // Nunca oculta la opcion ya seleccionada -- un Tema existente puede
            // legitimamente tener bloques del otro grupo (contenido mas viejo
            // mezclado) y no hay que perder ese valor de vista silenciosamente.
            var visible = permitidos.indexOf(opt.value) !== -1 || opt.value === valorActual;
            opt.hidden = !visible;
            opt.disabled = !visible;
        });
    }

    function filtrarFieldsets(fila, permitidos, valorActual) {
        var nombrePermitido = {};
        permitidos.forEach(function (v) { nombrePermitido[FIELDSET_DE_TIPO[v]] = true; });
        // El fieldset del valor YA seleccionado en esta fila queda visible
        // siempre, aunque no pertenezca al grupo activo -- mismo criterio que
        // filtrarSelectTipo: no esconder contenido ya cargado.
        if (valorActual && FIELDSET_DE_TIPO[valorActual]) {
            nombrePermitido[FIELDSET_DE_TIPO[valorActual]] = true;
        }
        var headings = fila.querySelectorAll('h4.fieldset-heading, h2.fieldset-heading');
        Array.prototype.forEach.call(headings, function (h) {
            var fieldset = h.closest('fieldset');
            if (!fieldset) return;
            var texto = h.textContent.trim();
            if (!esNombreConocido(texto)) return; // fieldset ajeno a este mecanismo (ej. "Bloques de Contenido"), no tocar
            fieldset.style.display = nombrePermitido[texto] ? '' : 'none';
        });
    }

    var NOMBRES_CONOCIDOS = null;
    function esNombreConocido(texto) {
        if (!NOMBRES_CONOCIDOS) {
            NOMBRES_CONOCIDOS = {};
            Object.keys(FIELDSET_DE_TIPO).forEach(function (v) { NOMBRES_CONOCIDOS[FIELDSET_DE_TIPO[v]] = true; });
        }
        return !!NOMBRES_CONOCIDOS[texto];
    }

    function filtrarTodos() {
        var selectTema = document.getElementById('id_tipo');
        if (!selectTema) return;
        var permitidos = tiposPermitidos(selectTema.value);

        var filas = document.querySelectorAll('.inline-group .inline-related');
        Array.prototype.forEach.call(filas, function (fila) {
            var selectBloque = fila.querySelector('select[name$="-tipo"]');
            if (!selectBloque) return;
            filtrarSelectTipo(selectBloque, permitidos);
            filtrarFieldsets(fila, permitidos, selectBloque.value);

            // Si el bloque cambia su PROPIO tipo (no el del Tema), el
            // fieldset a mostrar tambien cambia -- ej. recien creado, sin
            // fila guardada todavia.
            if (!selectBloque._temaBloquesTipoListener) {
                selectBloque._temaBloquesTipoListener = true;
                selectBloque.addEventListener('change', function () {
                    filtrarFieldsets(fila, tiposPermitidos(selectTema.value), selectBloque.value);
                });
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        var selectTema = document.getElementById('id_tipo');
        if (!selectTema) return;

        filtrarTodos();
        selectTema.addEventListener('change', filtrarTodos);

        // Filas de bloque agregadas dinamicamente con "Añadir otro Bloque de
        // Contenido" -- django.jQuery dispara este evento propio despues de
        // clonar la fila vacia, es el gancho documentado para esto.
        if (window.django && window.django.jQuery) {
            window.django.jQuery(document).on('formset:added', function () {
                filtrarTodos();
            });
        }
    });
})();
