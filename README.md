# Música Digital

Plataforma de entrenamiento auditivo, lectura a primera vista y práctica musical, hecha con Django 5.2. Un solo app (`trainer`) concentra modelos, vistas y URLs — no hay capa de API/DRF, los endpoints son vistas Django comunes que devuelven `JsonResponse`. Ver `CLAUDE.md` para el detalle de arquitectura pensado para trabajar sobre el código con asistencia de IA; este README es la referencia operativa (cómo correr cosas).

## Puesta en marcha

```bash
python manage.py runserver          # servidor de desarrollo
python manage.py migrate            # aplicar migraciones
python manage.py makemigrations trainer
python manage.py createsuperuser
python manage.py test trainer       # tests (trainer/tests.py está vacío por ahora)
```

Variables de entorno relevantes (ver `config/settings.py` para el detalle completo):

- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` — configuración estándar de Django.
- `ANTHROPIC_TEST_API_KEY` — necesaria para la función de análisis orquestal con IA (`orquestador_analizar`).
- `SCORE_FILE_ENCRYPTION_KEY` — clave Fernet para cifrar en reposo los archivos de partitura subidos (`ScoreAnalysis.score_file`). Sin default a propósito — perderla deja inaccesibles todos los archivos ya subidos.

### Scripts de semilla (raíz del repo)

`seed_pieces.py`, `seed_gamification.py`, `seed_dictado.py`, `seed_solfeo.py`, `seed_recommended.py` — se corren directo (`python seed_x.py`, no vía `manage.py`), cada uno llama a `django.setup()` por su cuenta. Pueblan `Game`, `Achievement`, `Piece`, `DailyGoal`. Leer el script antes de correrlo para saber qué pisa.

## Herramientas locales de fabricación de contenido

Dos `management commands` viven en `trainer/management/commands/` pero **no son features del sitio**: son herramientas de curación que corro en mi máquina para preparar material — nunca se ejecutan en PythonAnywhere, no tocan la base de datos, no dependen de nada del sitio (`trainer/models.py`/`views.py`/`urls.py`), y no agregan dependencias nuevas más allá de `music21` (ya usada por el sitio) y la librería estándar.

Comparten un módulo interno, `trainer/management/commands/_comun.py` (el `_` inicial hace que Django lo ignore al listar comandos — no aparece como un `manage.py` fantasma): ahí vive `identificar_voces()` (criterio común de "voz superior = parte de pitch promedio más alto") y `EscritorLog` (logging a consola + `log.txt` en la carpeta de salida, sin depender de `settings.LOGGING`).

Ambas herramientas comparten el mismo contrato de robustez: **solo lectura sobre la carpeta de entrada**, nunca tocan los archivos originales; si una pieza no parsea o algo explota, se loguea el error y el batch sigue con la siguiente — un archivo roto nunca aborta la corrida completa; y cada archivo de salida se verifica **reparseándolo** antes de darlo por bueno (mismo criterio que usa el generador de partituras orquestales del sitio) — si no pasa la verificación, se descarta y se loguea, no se escribe un archivo a medias.

### `triturar_piezas` — cortar piezas largas en excerpts de solfeo

```bash
python manage.py triturar_piezas <carpeta_entrada> <carpeta_salida>
```

Toma piezas completas (`.musicxml`/`.xml`/`.mxl`) y las corta en excerpts de **8 a 16 compases**, candidatos a ejercicio de solfeo, cortando solo en puntos musicalmente válidos: prioriza una **cadencia** (nota larga de la voz superior sobre tónica o dominante de la tonalidad local, con bonus si el bajo confirma movimiento V-I), después un **fin de frase** (nota o silencio largo en la voz superior), y como último recurso un **corte forzado** a los 16 compases (marcado `corte_forzado: true` para mirarlo con más desconfianza al curar). Calcula además un set de métricas determinísticas por excerpt (ámbito, saltos, variedad rítmica, tonalidad detectada, dificultad estimada) para poder filtrar/ordenar en planilla antes de abrir cada archivo.

Casos particulares ya resueltos:
- **Anacrusa** (compás de pickup, `number=0`/`paddingLeft>0`): se detecta y se incorpora gratis al inicio del primer excerpt de la pieza (sin contar para el mínimo/máximo de 8-16), con `compas_inicio: 0` e `incluye_anacrusa: true` en los metadatos. Queda logueado siempre que se detecta.
- **Resto final corto**: si al final de la pieza queda un remanente de menos de 8 compases, se fusiona con el excerpt anterior si el total no supera 16; si no entra, se descarta (logueado).
- **Pieza demasiado corta** (menos de 8 compases en total): se omite entera, logueado.

Salida:

```
carpeta_salida/
  <pieza>/
    <pieza>_ex01.musicxml
    <pieza>_ex01.json      # métricas + compases de origen + tipo de corte
    <pieza>_ex02.musicxml
    <pieza>_ex02.json
    ...
  resumen.csv               # una fila por excerpt, todas las métricas -- para ordenar/filtrar
  log.txt                   # todo lo logueado durante la corrida
```

Los umbrales de dificultad (`UMBRALES_DIFICULTAD`, `PUNTAJE_A_ETIQUETA`) están todos juntos al principio del archivo, pensados para ajustar sin tocar el resto de la lógica.

### `reducir_a_piano` — reducción de piano de partituras de cámara

```bash
python manage.py reducir_a_piano <carpeta_entrada> <carpeta_salida>
```

Toma partituras de cámara multi-parte (piezas enteras o excerpts ya triturados, da igual) y genera una **reducción de piano a dos pentagramas** de cada una: la voz melódica (parte de pitch promedio más alto) se copia tal cual como mano derecha; el resto de las partes se colapsan como mano izquierda, a **altura real sonada** (si hay instrumentos transpositores como contrabajo, se convierte explícitamente con `toSoundingPitch()` antes de nada más — la reducción representa lo que suena, no lo escrito). Pensada como insumo para curar en MuseScore y subir después como `FragmentoOrquestacion` (el ejercicio de pintado de orquestación).

La mano izquierda se arma **correlacionando directamente las partes originales** (no con `chordify()`) — cada altura de la mano izquierda sabe de qué instrumento vino desde el momento en que se extrae, sin necesidad de adivinarlo después por coincidencia de altura/offset (que sería ambiguo justo en los pasajes de doblaje, que son los que más importan para la trazabilidad).

Reglas de limpieza (el valor real de la herramienta frente a un chordify crudo), en este orden:
1. **Anti-duplicación**: se elimina de la mano izquierda cualquier altura que duplique a la melodía en unísono u octava (comparación por clase de altura). Si un acorde queda vacío, se extiende la duración del acorde anterior de la mano izquierda para cubrir el hueco (no se inserta un silencio, salvo que sea el primerísimo tramo de la pieza y no haya nada previo que extender).
2. **Plegado de registro**: una altura que cruza por encima de la melodía se pliega una octava abajo; un acorde que excede 24 semitonos de ámbito se pliega hacia adentro (nota más aguda, una octava por vez) hasta entrar en rango.
3. **Des-densificación**: tramos consecutivos con el mismo contenido de alturas se fusionan en una sola nota/acorde de duración combinada (análogo a fusionar ligaduras: se representa lo que se sostiene, no cada re-ataque administrativo que generaría un chordify crudo).

Salida:

```
carpeta_salida/
  <pieza>_piano.musicxml    # PartStaff + StaffGroup (brace) -- un piano de verdad, no 2 instrumentos sueltos
  <pieza>_piano.json        # trazabilidad nota por nota + resumen de limpieza
  log.txt
```

El JSON de trazabilidad (pensado a futuro para comparar la orquestación del alumno contra la original — todavía no se usa en el sitio) trae, por cada nota final de la reducción: `{mano, compas, offset, duracion_ql, pitch, origen}` — `origen` es siempre una lista de nombres de instrumento (aunque para la mano derecha tenga un solo elemento), y estos datos se derivan del MusicXML ya barreado (después de `makeMeasures()`+`makeTies()`), así que reflejan exactamente los mismos segmentos ligados que el ejercicio de pintado va a mostrar — no una agrupación interna previa a la fusión. También trae `resumen_limpieza` por pieza (`notas_eliminadas_por_doblaje`, `notas_plegadas`, `tramos_fusionados`) como señal rápida de cuánto tocó la limpieza antes de abrir el archivo en MuseScore.

### Conversión ABC → MusicXML (paso previo, manual)

Si el material de origen es ABC (ej. salida de NotaGen), se convierte con MuseScore antes de pasarlo a cualquiera de las dos herramientas:

```powershell
# Un archivo:
& "C:\Program Files\MuseScore 4\bin\MuseScore4.exe" input.abc -o output.musicxml

# Carpeta completa (PowerShell):
Get-ChildItem *.abc | ForEach-Object {
    & "C:\Program Files\MuseScore 4\bin\MuseScore4.exe" $_.FullName -o ($_.BaseName + ".musicxml")
}
```
