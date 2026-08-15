"""
Borra el corpus de partituras de ejemplo bundled con music21 (Bach, folk songs, etc.
-- music21/corpus/*), sin tocar el CÓDIGO del propio módulo corpus (que vive mezclado
en la misma carpeta y hace falta para que "import music21" no se rompa -- ver más
abajo). Confirmado por auditoría: este proyecto nunca llama a music21.corpus.parse()
ni a nada del módulo corpus -- todo el pipeline trabaja sobre archivos subidos por
usuarios o generados con NotaGen, nunca sobre el corpus bundled.

Por qué no alcanza con "rm -rf music21/corpus/" a secas: esa carpeta no es solo datos.
Tiene 6 archivos .py (corpus/__init__.py, chorales.py, corpora.py, manager.py,
testCorpus.py, virtual.py, work.py -- ~160 KB en total) que son el CÓDIGO del módulo
corpus, y music21/__init__.py los importa sin condición al cargar el paquete (a través
de music21.test.testRunner -> music21.test.test_base -> from music21 import corpus).
Sin esos .py, ni siquiera "import music21" funciona -- confirmado empíricamente antes
de escribir este script. Lo que sí se puede borrar sin problema son las ~37
subcarpetas de datos (bach/, beethoven/, trecento/, etc. -- ahí está el 99% de las
~64 MB), que este script mueve a un backup temporal, verifica que todo sigue andando,
y recién ahí borra de verdad.

Uso (en el entorno virtual real, ej. workon md-env en PythonAnywhere):
    python limpiar_corpus_music21.py

Reversible mientras el script está corriendo (el backup temporal solo se borra al
final, después de confirmar que "import music21" sigue funcionando). Idempotente: si
ya se corrió antes, no encuentra nada para mover y no rompe nada.

Hay que volver a correrlo cada vez que se reprovisiona el entorno desde cero
(pip install -r requirements.txt reinstala music21 con el corpus completo de nuevo --
esto no es un default de pip que se pueda desactivar, ver la investigación de la
sesión que agregó este script).
"""
import os
import shutil
import subprocess
import sys


def main():
    import music21
    corpus_dir = os.path.join(os.path.dirname(music21.__file__), 'corpus')

    if not os.path.isdir(corpus_dir):
        print(f"No se encontró {corpus_dir} -- ¿music21 no está instalado en este entorno?")
        sys.exit(1)

    entradas = os.listdir(corpus_dir)
    subdirs_datos = [e for e in entradas if os.path.isdir(os.path.join(corpus_dir, e))]

    if not subdirs_datos:
        print(f"{corpus_dir} ya no tiene subcarpetas de datos -- nada para hacer (idempotente).")
        return

    tamano_antes = _tamano_mb(os.path.dirname(music21.__file__))
    print(f"music21/ pesa {tamano_antes:.0f} MB antes de limpiar.")
    print(f"Subcarpetas de datos encontradas en corpus/: {len(subdirs_datos)}")

    backup_dir = os.path.join(os.path.dirname(corpus_dir), '_corpus_data_backup_temp')
    os.makedirs(backup_dir, exist_ok=True)

    movidos = []
    try:
        for entry in subdirs_datos:
            origen = os.path.join(corpus_dir, entry)
            destino = os.path.join(backup_dir, entry)
            shutil.move(origen, destino)
            movidos.append(entry)

        print(f"Movidas {len(movidos)} subcarpetas a backup temporal ({backup_dir}).")
        print("Verificando que 'import music21' sigue funcionando (proceso nuevo, en frío)...")

        resultado = subprocess.run(
            [sys.executable, '-c', 'import music21; print(music21.__version__)'],
            capture_output=True, text=True,
        )
        if resultado.returncode != 0:
            raise RuntimeError(
                f"'import music21' falló después de mover los datos -- revirtiendo. "
                f"stderr: {resultado.stderr}"
            )
        print(f"OK: import music21 {resultado.stdout.strip()} funciona sin los datos del corpus.")

    except Exception as e:
        print(f"\nALGO FALLÓ ({e}) -- restaurando todo antes de salir, no se borra nada.")
        for entry in movidos:
            shutil.move(os.path.join(backup_dir, entry), os.path.join(corpus_dir, entry))
        shutil.rmtree(backup_dir, ignore_errors=True)
        print("Restaurado. El entorno queda exactamente como estaba antes de correr este script.")
        sys.exit(1)

    # Solo se llega acá si la verificación de arriba pasó -- recién ahora se borra de verdad.
    shutil.rmtree(backup_dir)
    tamano_despues = _tamano_mb(os.path.dirname(music21.__file__))
    print(f"\nBackup temporal borrado -- espacio liberado de verdad.")
    print(f"music21/ pesaba {tamano_antes:.0f} MB, ahora pesa {tamano_despues:.0f} MB "
          f"(se liberaron ~{tamano_antes - tamano_despues:.0f} MB).")


def _tamano_mb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1024 / 1024


if __name__ == '__main__':
    main()
