from django.contrib import admin
from .models import AIAssessment, AssessmentReview, Prescription

admin.site.register(AIAssessment)
admin.site.register(AssessmentReview)
admin.site.register(Prescription)