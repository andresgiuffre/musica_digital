"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from pagos.urls import webhook_urlpatterns

# Solo /admin/ y los webhooks de pagos quedan AFUERA de i18n_patterns() -- /admin/
# tiene su propio manejo de idioma interno (via Accept-Language/USE_I18N), meterlo
# bajo /es//en/ es la fuente clásica de bugs de doble-idioma en el admin de Django.
# Los webhooks de pagos.urls (MercadoPago/PayPal) son llamados servidor-a-servidor,
# no por un navegador -- no tienen "idioma" que negociar, y si LocaleMiddleware
# alguna vez los redirigiera (el mismo riesgo que ya motiva sacar /admin/ de acá),
# el proveedor vería eso como una entrega fallida silenciosa, no un bug cosmético
# que alguien note mirando la pantalla.
urlpatterns = [
    path('admin/', admin.site.urls),
]
urlpatterns += webhook_urlpatterns

# prefix_default_language=False: español (LANGUAGE_CODE, el default) sirve
# SIN prefijo en la raíz (/biblioteca/) -- cero links/bookmarks rotos sobre
# el sitio actual. Inglés lleva prefijo explícito (/en/biblioteca/).
#
# OJO -- comportamiento real de LocaleMiddleware con prefix_default_language
# =False, no intuitivo y confirmado empíricamente (no asumido): CUALQUIER URL
# sin prefijo se fuerza SIEMPRE al idioma default, ignorando sesión/cookie/
# Accept-Language -- no es "se negocia", es "el path manda". Ver
# django.middleware.locale.LocaleMiddleware.process_request: si
# get_language_from_path() no encuentra prefijo Y prefixed_default_language
# es False, pisa lo que haya detectado por cookie/header y usa
# settings.LANGUAGE_CODE directo. Consecuencia real: /i18n/setlang/ (el
# endpoint que procesa el cambio de idioma) TIENE que vivir él mismo bajo
# i18n_patterns() -- si quedara afuera (sin prefijo), toda request a ese
# endpoint se resolvería con idioma ambiente forzado a español sin importar
# desde qué idioma se esté cambiando, y translate_url() (la función que
# arma la URL de destino con el prefijo correcto) fallaría en silencio para
# cualquier next= que ya tuviera el prefijo /en/ -- confirmado con un
# repro directo antes de este comentario, no es una precaución especulativa.
# Adentro de i18n_patterns(), la propia request a /en/i18n/setlang/ trae su
# prefijo real, LocaleMiddleware detecta el idioma correcto sin pisarlo, y
# translate_url() arma bien la URL en ambas direcciones.
#
# django.contrib.auth.urls va adentro a propósito: login/logout/password
# tienen UI visible para el usuario final, deben respetar el idioma elegido
# igual que el resto del sitio.
urlpatterns += i18n_patterns(
    path('i18n/', include('django.conf.urls.i18n')),
    path('', include('trainer.urls')),
    path('', include('pagos.urls')),
    path('', include('django.contrib.auth.urls')),
    prefix_default_language=False,
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
