from django.contrib import admin
from .models import ClinicianAction, ClinicianAvailability, PatientAssignment

admin.site.register(ClinicianAction)
admin.site.register(ClinicianAvailability)
admin.site.register(PatientAssignment)