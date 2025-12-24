import uuid
from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.utils import timezone
from datetime import timedelta
from apps.authentication.models import User
from apps.conversations.models import ConversationSession


class ClinicianAvailability(models.Model):
    """Track clinician availability status."""
    
    STATUS_CHOICES = [
        ('AVAILABLE', 'Available'),
        ('ON_CALL', 'On Call'),
        ('BUSY', 'Busy'),
        ('OFFLINE', 'Offline'),
        ('ON_LEAVE', 'On Leave'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinician = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='availability',
        limit_choices_to={'role': 'CLINICIAN'}
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='AVAILABLE')
    shift_start = models.TimeField(null=True, blank=True)
    shift_end = models.TimeField(null=True, blank=True)
    current_patient_count = models.IntegerField(default=0)
    accepts_specializations = models.JSONField(default=list, blank=True)
    last_activity = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "Clinician Availability"
    
    def __str__(self):
        return f"{self.clinician.get_full_name()} - {self.status}"
    
    def is_within_shift(self):
        """Check if current time is within clinician's shift."""
        if not self.shift_start or not self.shift_end:
            return True
        
        now = timezone.now().time()
        return self.shift_start <= now <= self.shift_end
    
    def can_accept_patient(self):
        """Check if clinician can accept new patient."""
        from apps.authentication.models import ClinicianProfile
        
        try:
            profile = ClinicianProfile.objects.get(user=self.clinician)
            can_accept = (
                self.status in ['AVAILABLE', 'ON_CALL'] and
                self.current_patient_count < profile.max_concurrent_patients and
                profile.is_available and
                self.is_within_shift()
            )
            return can_accept
        except ClinicianProfile.DoesNotExist:
            return False


class PatientAssignment(models.Model):
    """Track patient-clinician assignments."""
    
    ASSIGNMENT_REASON_CHOICES = [
        ('AUTO_MATCH', 'Automatic Match'),
        ('ESCALATION', 'Escalation'),
        ('MANUAL', 'Manual Assignment'),
        ('SPECIALIST_REFERRAL', 'Specialist Referral'),
        ('TRANSFER', 'Transfer'),
    ]
    
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('COMPLETED', 'Completed'),
        ('TRANSFERRED', 'Transferred'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='clinician_assignments',
        limit_choices_to={'role': 'PATIENT'}
    )
    clinician = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='patient_assignments',
        limit_choices_to={'role': 'CLINICIAN'}
    )
    conversation = models.ForeignKey(
        ConversationSession, on_delete=models.CASCADE, related_name='assignments'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    assignment_reason = models.CharField(max_length=20, choices=ASSIGNMENT_REASON_CHOICES)
    assigned_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    transferred_to = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transferred_assignments', limit_choices_to={'role': 'CLINICIAN'}
    )
    
    class Meta:
        ordering = ['-assigned_at']
        indexes = [
            models.Index(fields=['clinician', 'status']),
            models.Index(fields=['patient', 'status']),
        ]
    
    def __str__(self):
        return f"{self.patient.phone_number} -> {self.clinician.get_full_name() if self.clinician else 'Unassigned'}"
    
    def mark_completed(self):
        """Mark assignment as completed."""
        self.status = 'COMPLETED'
        self.completed_at = timezone.now()
        self.save()


class ClinicianAction(models.Model):
    """Audit log of clinician actions."""
    
    ACTION_TYPE_CHOICES = [
        ('ASSESSMENT_VIEWED', 'Assessment Viewed'),
        ('ASSESSMENT_REVIEWED', 'Assessment Reviewed'),
        ('ASSESSMENT_APPROVED', 'Assessment Approved'),
        ('ASSESSMENT_MODIFIED', 'Assessment Modified'),
        ('ASSESSMENT_REJECTED', 'Assessment Rejected'),
        ('ASSESSMENT_SENT', 'Assessment Sent to Patient'),
        ('MESSAGE_SENT', 'Message Sent'),
        ('MESSAGE_RECEIVED', 'Message Received'),
        ('PATIENT_ASSIGNED', 'Patient Assigned'),
        ('ESCALATION_HANDLED', 'Escalation Handled'),
        ('AVAILABILITY_UPDATED', 'Availability Updated'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinician = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='clinician_actions',
        limit_choices_to={'role': 'CLINICIAN'}
    )
    conversation = models.ForeignKey(
        ConversationSession, on_delete=models.CASCADE, related_name='clinician_actions'
    )
    action_type = models.CharField(max_length=30, choices=ACTION_TYPE_CHOICES)
    action_details = models.JSONField(default=dict, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['clinician', 'timestamp']),
            models.Index(fields=['action_type', 'timestamp']),
        ]
    
    def __str__(self):
        return f"{self.clinician.get_full_name()} - {self.action_type}"