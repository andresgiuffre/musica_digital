from cryptography.fernet import Fernet
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage


def _get_fernet():
    key = getattr(settings, 'SCORE_FILE_ENCRYPTION_KEY', None)
    if not key:
        raise RuntimeError(
            "SCORE_FILE_ENCRYPTION_KEY no está configurada. Sin esa variable de entorno "
            "no se puede cifrar ni descifrar ningún archivo del analizador — ver el "
            "comentario junto a SCORE_FILE_ENCRYPTION_KEY en config/settings.py."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


class EncryptedFileSystemStorage(FileSystemStorage):
    """
    Cifra con Fernet el contenido de cada archivo antes de escribirlo a disco, y lo
    descifra al leerlo. El resto del código sigue usando la API normal de FileField
    (.open(), .read()) sin saber que el archivo está cifrado en disco.
    """

    def _save(self, name, content):
        fernet = _get_fernet()
        contenido_cifrado = fernet.encrypt(content.read())
        return super()._save(name, ContentFile(contenido_cifrado))

    def _open(self, name, mode='rb'):
        fernet = _get_fernet()
        archivo_cifrado = super()._open(name, mode)
        try:
            contenido_descifrado = fernet.decrypt(archivo_cifrado.read())
        finally:
            archivo_cifrado.close()
        return ContentFile(contenido_descifrado, name=name)
