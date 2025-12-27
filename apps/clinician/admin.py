from django.contrib import admin
from .models import ClinicianAction, ClinicianAvailability, PatientAssignment, ModificationSession

admin.site.register(ClinicianAction)
admin.site.register(ClinicianAvailability)
admin.site.register(PatientAssignment)
admin.site.register(ModificationSession)