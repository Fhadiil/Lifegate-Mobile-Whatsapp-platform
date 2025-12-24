from django.contrib import admin
from .models import EscalationAlert, EscalationRule, EscalationHistory

admin.site.register(EscalationAlert)
admin.site.register(EscalationRule)
admin.site.register(EscalationHistory)