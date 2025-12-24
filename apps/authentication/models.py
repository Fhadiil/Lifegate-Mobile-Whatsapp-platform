import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator


class User(AbstractUser):
    """Extended User model with phone and role."""
    
    ROLE_CHOICES = [
        ('PATIENT', 'Patient'),
        ('CLINICIAN', 'Clinician'),
        ('ADMIN', 'Administrator'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone_number = models.CharField(max_length=20, unique=True)
    whatsapp_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='PATIENT')
    is_active_in_system = models.BooleanField(default=True)
    terms_accepted = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    last_activity = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone_number']),
            models.Index(fields=['whatsapp_id']),
            models.Index(fields=['role']),
        ]
    
    def __str__(self):
        return f"{self.get_full_name() or self.phone_number} ({self.role})"


class PatientProfile(models.Model):
    """Patient-specific profile information."""
    
    GENDER_CHOICES = [
        ('MALE', 'Male'),
        ('FEMALE', 'Female'),
        ('OTHER', 'Other'),
        ('PREFER_NOT_TO_SAY', 'Prefer not to say'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='patient_profile')
    date_of_birth = models.DateField(null=True, blank=True)
    age = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(150)])
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, null=True, blank=True)
    medical_history = models.TextField(blank=True, help_text="Previous medical conditions")
    current_medications = models.TextField(blank=True, help_text="Currently taking medications")
    allergies = models.TextField(blank=True, help_text="Known allergies")
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    preferred_language = models.CharField(max_length=10, default='en')
    total_consultations = models.IntegerField(default=0)
    last_consultation_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "Patient Profiles"
    
    def __str__(self):
        return f"Profile: {self.user.phone_number}"


class ClinicianProfile(models.Model):
    """Clinician-specific profile and qualifications."""
    
    SPECIALIZATION_CHOICES = [
        ('GENERAL_MEDICINE', 'General Medicine'),
        ('CARDIOLOGY', 'Cardiology'),
        ('NEUROLOGY', 'Neurology'),
        ('PEDIATRICS', 'Pediatrics'),
        ('PSYCHIATRY', 'Psychiatry'),
        ('DERMATOLOGY', 'Dermatology'),
        ('OTHER', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='clinician_profile')
    license_number = models.CharField(max_length=50, unique=True)
    license_expiry = models.DateField(null=True, blank=True)
    specialization = models.CharField(max_length=50, choices=SPECIALIZATION_CHOICES)
    hospital_affiliation = models.CharField(max_length=200, blank=True)
    is_available = models.BooleanField(default=True)
    max_concurrent_patients = models.IntegerField(
        default=15,
        validators=[MinValueValidator(1), MaxValueValidator(50)]
    )
    response_sla_hours = models.IntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(24)]
    )
    total_patients_handled = models.IntegerField(default=0)
    total_assessments_reviewed = models.IntegerField(default=0)
    avg_review_time_minutes = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "Clinician Profiles"
    
    def __str__(self):
        return f"Dr. {self.user.get_full_name() or self.license_number}"
    
    def get_current_patient_count(self):
        """Get current active patient count."""
        from apps.clinician.models import PatientAssignment
        return PatientAssignment.objects.filter(
            clinician=self.user,
            status='ACTIVE'
        ).count()
    
    def can_accept_patients(self):
        """Check if clinician can accept more patients."""
        return self.get_current_patient_count() < self.max_concurrent_patients and self.is_available