import uuid
import json
from django.db import models
from django.contrib.postgres.fields import ArrayField
from apps.authentication.models import User
from apps.conversations.models import ConversationSession
from django.utils import timezone


class AIAssessment(models.Model):
    """AI-generated clinical assessment."""
    
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('GENERATED', 'Generated'),
        ('PENDING_REVIEW', 'Pending Review'),
        ('APPROVED', 'Approved'),
        ('MODIFIED', 'Modified'),
        ('REJECTED', 'Rejected'),
        ('SENT_TO_PATIENT', 'Sent to Patient'),
        ('EXPIRED', 'Expired'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.OneToOneField(
        ConversationSession, on_delete=models.CASCADE, related_name='assessment'
    )
    patient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='assessments'
    )
    patient_age = models.IntegerField(null=True)
    patient_gender = models.CharField(max_length=20, blank=True)
    chief_complaint = models.TextField()
    
    # Structured assessment data (JSON)
    symptoms_overview = models.JSONField(default=dict, blank=True)
    key_observations = models.JSONField(default=dict, blank=True)
    preliminary_recommendations = models.JSONField(default=dict, blank=True)
    otc_suggestions = models.JSONField(default=dict, blank=True)
    monitoring_advice = models.JSONField(default=dict, blank=True)
    red_flags_detected = models.JSONField(default=list, blank=True)
    confidence_score = models.FloatField(default=0.0, validators=[])
    
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='DRAFT',
        db_index=True
    )
    assessment_notes = models.TextField(blank=True)
    
    generated_at = models.DateTimeField(auto_now_add=True)
    sent_to_patient_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['patient', 'status']),
            models.Index(fields=['status', 'generated_at']),
        ]
    
    def __str__(self):
        return f"Assessment {self.id} - {self.chief_complaint[:50]}"
    
    def to_patient_format(self):
        """Format assessment for patient viewing."""
        assessment = {
            'id': str(self.id),
            'chief_complaint': self.chief_complaint,
            'symptoms': self.symptoms_overview.get('primary_symptoms', []),
            'likely_cause': self.key_observations.get('likely_condition', ''),
            'medications': self.otc_suggestions.get('medications', []),
            'recommendations': self.preliminary_recommendations.get('lifestyle_changes', []),
            'monitoring': self.monitoring_advice.get('what_to_monitor', []),
            'when_to_seek_help': self.monitoring_advice.get('when_to_seek_help', []),
            'confidence': f"{int(self.confidence_score * 100)}%",
        }
        return assessment


class AssessmentReview(models.Model):
    """Clinician review and modification of assessment."""
    
    ACTION_CHOICES = [
        ('APPROVED', 'Approved'),
        ('MODIFIED', 'Modified'),
        ('REJECTED', 'Rejected'),
        ('NEEDS_MORE_INFO', 'Needs More Information'),
    ]
    
    RISK_LEVEL_CHOICES = [
        ('LOW', 'Low Risk'),
        ('MODERATE', 'Moderate Risk'),
        ('HIGH', 'High Risk'),
        ('CRITICAL', 'Critical Risk'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assessment = models.ForeignKey(
        AIAssessment, on_delete=models.CASCADE, related_name='reviews'
    )
    clinician = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='assessment_reviews',
        limit_choices_to={'role': 'CLINICIAN'}
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    clinician_notes = models.TextField(blank=True)
    
    # Modified fields if action is MODIFIED
    modified_recommendations = models.JSONField(null=True, blank=True)
    modified_otc_suggestions = models.JSONField(null=True, blank=True)
    modified_monitoring_advice = models.JSONField(null=True, blank=True)
    
    clinician_risk_level = models.CharField(
        max_length=20, choices=RISK_LEVEL_CHOICES, default='MODERATE'
    )
    requires_urgent_follow_up = models.BooleanField(default=False)
    follow_up_days = models.IntegerField(null=True, blank=True)
    
    review_started_at = models.DateTimeField(auto_now_add=True)
    review_completed_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-review_completed_at']
        indexes = [
            models.Index(fields=['assessment', 'action']),
            models.Index(fields=['clinician', 'review_completed_at']),
        ]
    
    def __str__(self):
        return f"Review {self.id} - {self.action}"
    
    def get_final_assessment(self):
        """Get final assessment data (original or modified)."""
        if self.action == 'MODIFIED':
            assessment_data = {
                'recommendations': self.modified_recommendations or self.assessment.preliminary_recommendations,
                'otc_suggestions': self.modified_otc_suggestions or self.assessment.otc_suggestions,
                'monitoring_advice': self.modified_monitoring_advice or self.assessment.monitoring_advice,
                'clinician_notes': self.clinician_notes,
            }
        else:
            assessment_data = {
                'recommendations': self.assessment.preliminary_recommendations,
                'otc_suggestions': self.assessment.otc_suggestions,
                'monitoring_advice': self.assessment.monitoring_advice,
                'clinician_notes': self.clinician_notes,
            }
        return assessment_data
    

class Prescription(models.Model):
    """
    Prescription document record.
    Tracks all prescriptions issued to patients.
    """
    
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('SENT', 'Sent to Patient'),
        ('PRINTED', 'Printed'),
        ('FILLED', 'Filled at Pharmacy'),
        ('EXPIRED', 'Expired'),
        ('VOIDED', 'Voided'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Relationships
    assessment = models.ForeignKey(
        'AIAssessment', 
        on_delete=models.CASCADE, 
        related_name='prescriptions'
    )
    patient = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='prescriptions',
        limit_choices_to={'role': 'PATIENT'}
    )
    clinician = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True,
        related_name='issued_prescriptions',
        limit_choices_to={'role': 'CLINICIAN'}
    )
    
    # Content
    medications = models.JSONField(help_text="Prescribed medications list")
    recommendations = models.JSONField(help_text="Doctor's recommendations")
    warnings = models.JSONField(help_text="Emergency warning signs")
    notes = models.TextField(blank=True, help_text="Doctor's notes")
    
    # PDF File
    pdf_file = models.FileField(
        upload_to='prescriptions/%Y/%m/%d/',
        blank=True,
        null=True,
        help_text="PDF prescription document"
    )
    
    # Status
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='SENT'
    )
    
    # Validity
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(help_text="Prescription expiry date (typically 30 days)")
    sent_at = models.DateTimeField(null=True, blank=True)
    printed_at = models.DateTimeField(null=True, blank=True)
    
    # Audit
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-issued_at']
        indexes = [
            models.Index(fields=['patient', 'issued_at']),
            models.Index(fields=['clinician', 'issued_at']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"Prescription {self.id} - {self.patient.phone_number}"
    
    def save(self, *args, **kwargs):
        # Set expiry date if not set (30 days from now)
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(days=30)
        
        # Set sent_at when status becomes SENT
        if self.status == 'SENT' and not self.sent_at:
            self.sent_at = timezone.now()
        
        super().save(*args, **kwargs)
    
    @property
    def is_valid(self):
        """Check if prescription is still valid."""
        return self.status not in ['EXPIRED', 'VOIDED'] and timezone.now() <= self.expires_at
    
    @property
    def days_remaining(self):
        """Days until prescription expires."""
        remaining = (self.expires_at - timezone.now()).days
        return max(0, remaining)
