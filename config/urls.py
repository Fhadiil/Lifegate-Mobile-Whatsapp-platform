from django.contrib import admin
from django.urls import path, include, re_path
from rest_framework import routers
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.system.views import TwilioWebhookView, HealthCheckView
from apps.clinician.views import ClinicianDashboardViewSet
from apps.conversations.views import ConversationViewSet
from apps.assessments.views import AssessmentViewSet
from apps.authentication.views import AuthViewSet
from django.conf import settings
from django.conf.urls.static import static
from apps.subscriptions.views import FlutterwaveWebhookView, PaymentSuccessView

router = routers.DefaultRouter()
router.register(r'clinician', ClinicianDashboardViewSet, basename='clinician')
router.register(r'conversations', ConversationViewSet, basename='conversations')
router.register(r'assessments', AssessmentViewSet, basename='assessments')
router.register(r'auth', AuthViewSet, basename='auth')

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Auth
    path('api/v1/auth/', include('apps.authentication.urls')),
    
    # API
    path('api/v1/health/', HealthCheckView.as_view(), name='health'),
    path('api/v1/system/twilio-webhook/', TwilioWebhookView.as_view(), name='twilio_webhook'),
    path('api/v1/webhooks/flutterwave/', FlutterwaveWebhookView.as_view(), name='flutterwave_webhook'),
    path('api/v1/', include(router.urls)),
    path('payment-success/', PaymentSuccessView.as_view(), name='payment_success'),
    
    
    # OpenAPI Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='docs'),
    
    
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

