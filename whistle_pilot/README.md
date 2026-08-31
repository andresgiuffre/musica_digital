# whistle_pilot

Piloto aislado (no depende de Django) para validar si transcribir un silbido
grabado a partitura simple es viable, antes de construir cualquier UI real.
Ver el docstring de `transcribe.py` para el detalle de cada paso.

Uso:

```
python transcribe.py --audio silbido.wav --bpm 90 --out-dir salida/
```

Flags relevantes para calibrar el pipeline: `--onset-delta` (sensibilidad del
detector de onsets), `--fusion-gap-max-ms` / `--umbral-confianza-corte`
(criterio de fusión post-onset), `--umbral-fragmentacion-hz` (umbral del
marcador de diagnóstico en el reporte), `--sin-fusion` (desactiva la fusión
para comparar antes/después).

## Limitación conocida: reataques reales sin pausa perceptible

Al calibrar la fusión post-onset contra una grabación real (`s4.wav`, un
silbido humano), encontramos que **un reataque real de la misma altura, sin
pausa de respiración perceptible, puede no dejar ninguna señal medible que lo
distinga de una nota sostenida con vibrato**. Confirmado empíricamente
probando tres señales candidatas, ninguna separó los dos casos:

- **Caída de confianza** (`voiced_probs` de pYIN): funciona en el `.wav`
  sintético (donde el reataque se simula con un dip de amplitud artificial al
  15%), pero un silbido real no necesariamente produce ese dip.
- **Gap de tiempo** entre las notas candidatas: en los casos problemáticos
  encontrados, el gap es exactamente 0 -- las candidatas quedan perfectamente
  contiguas, sin ningún hueco que medir.
- **Magnitud del onset** (`librosa.onset.onset_strength` en el punto de
  corte): se probó como alternativa a las dos anteriores; los valores de
  cortes espurios y de transiciones reales confirmadas se superponen
  (~1.1-1.8 en ambos casos), sin un umbral que los separe.

Tampoco ayuda subir `--onset-delta`: confirmado con un barrido (0.10 a 0.30)
contra `s4.wav`, la duración de un tramo fusionado no cambia con el delta --
el problema no es que el detector de onsets no encuentre suficientes cortes,
es que la fusión no tiene ninguna señal disponible para decidir dónde
terminaría una nota real y empezaría la siguiente dentro de un tramo continuo
a la misma altura.

**No es un bug pendiente del pipeline -- es una limitación conocida de la
técnica** (pitch tracking + onset detection sobre silbido monofónico). Va a
importar al diseñar la UI real: probablemente convenga guiarle al usuario a
silbar con una micro-pausa perceptible entre notas repetidas, en vez de
perseguir un detector que separe reataques silenciosos de vibrato dentro de
una nota sostenida -- ese problema, tal como está planteado, puede no tener
solución algorítmica confiable con esta técnica.

### Confirmado por oído en los dos sentidos posibles, sobre la misma grabación (`s4.wav`)

- **t≈2.68s-4.21s (D6, 1.53s continuos)**: escuchado y confirmado que es
  **una sola** nota sostenida real. El pipeline fusiona correctamente los ~13
  fragmentos crudos que el vibrato produjo ahí en una sola nota. Caso donde
  la fusión acierta.
- **t≈0.92s-1.45s (C6, 534ms continuos)**: escuchado y confirmado que en
  realidad son **dos silbidos reales distintos** -- el "Sol-Sol" con el que
  arranca Feliz Cumpleaños. El pipeline fusiona los 4 fragmentos crudos de
  esa zona en una sola nota, perdiendo la repetición real. Caso donde la
  fusión se equivoca -- no es un falso positivo de calibración, es la misma
  mecánica descripta arriba mostrando su costo real: dos notas genuinas
  desaparecen de la transcripción sin que haya ninguna señal en los datos
  para haberlas salvado.

Mismo mecanismo, mismos datos de entrada (gap 0, sin caída de confianza),
dos resultados opuestos -- la prueba más directa de que no hay heurística
posible acá sin información adicional (ej. que el usuario deje una pausa
real al silbar notas repetidas).
