from django.contrib import admin
from .models import CreditPackage, PatientSubscription, PaymentHistory

# Register your models here.
admin.site.register(CreditPackage)
admin.site.register(PatientSubscription)
admin.site.register(PaymentHistory)