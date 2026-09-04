from django.urls import path
from . import views, webhooks

# Páginas de usuario (checkout) -- incluidas DENTRO de i18n_patterns() en config/urls.py,
# igual que trainer.urls, porque sí son páginas navegadas por un usuario con idioma
# elegido.
urlpatterns = [
    path('pagos/checkout/plan/<slug:plan_codigo>/', views.checkout_iniciar_plan, name='checkout_iniciar_plan'),
    path('pagos/checkout/curso/<slug:curso_codigo>/', views.checkout_iniciar_curso, name='checkout_iniciar_curso'),
    path('pagos/checkout/retorno/', views.checkout_retorno, name='checkout_retorno'),
]

# Webhooks -- máquina a máquina, incluidos FUERA de i18n_patterns() en config/urls.py
# (junto a /admin/, misma razón ya documentada ahí: no hay "idioma" para un servidor de
# MercadoPago/PayPal, y una redirección de LocaleMiddleware sería una entrega
# silenciosamente perdida).
webhook_urlpatterns = [
    path('pagos/webhook/mercadopago/', webhooks.webhook_mercadopago, name='webhook_mercadopago'),
    path('pagos/webhook/paypal/', webhooks.webhook_paypal, name='webhook_paypal'),
]
