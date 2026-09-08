// Filtra las opciones del desplegable "tipo" de cada bloque de contenido (el
// StackedInline de BloqueContenido dentro del admin de Tema) segun el "Tipo de
// Tema" (Lectura/Practica) elegido en #id_tipo, para que la lista de tipos de
// actividad no siga creciendo sin limite a medida que se suman ejercicios.
//
// Espejo a mano de BloqueContenido.TIPOS_LECTURA/TIPOS_PRACTICA en models.py
// -- no se puede leer eso directamente desde aca (Python no corre en el
// navegador), asi que un tipo de bloque nuevo se agrega en AMBOS lugares.
(function () {
    'use strict';

    var TIPOS_LECTURA = ['TEXTO', 'EJEMPLO_PARTITURA', 'IMAGEN', 'VIDEO'];
    var TIPOS_PRACTICA = ['PRACTICA', 'PRACTICA_DIRIGIDA', 'RITMO_MATEMATICA', 'COMPLETAR_COMPAS', 'LINEAS_ESPACIOS'];

    function gruposPara(tipoTema) {
        return tipoTema === 'PRACTICA' ? TIPOS_PRACTICA : TIPOS_LECTURA;
    }

    function filtrarSelect(select, permitidos) {
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

    function filtrarTodos() {
        var selectTema = document.getElementById('id_tipo');
        if (!selectTema) return;
        var permitidos = gruposPara(selectTema.value);
        var selects = document.querySelectorAll('.inline-group select[name$="-tipo"]');
        Array.prototype.forEach.call(selects, function (select) {
            filtrarSelect(select, permitidos);
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
