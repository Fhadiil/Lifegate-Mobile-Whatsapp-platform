import uuid
from django.db import models
from apps.authentication.models import User


class AuditLog(models.Model):
    """HIPAA compliant audit logging."""
    
    ACTION_TYPE_CHOICES = [
        ('USER_LOGIN', 'User Login'),
        ('USER_LOGOUT', 'User Logout'),
        ('USER_CREATED', 'User Created'),
        ('USER_UPDATED', 'User Updated'),
        ('PROFILE_VIEWED', 'Profile Viewed'),
        ('MESSAGE_SENT', 'Message Sent'),
        ('MESSAGE_RECEIVED', 'Message Received'),
        ('ASSESSMENT_CREATED', 'Assessment Created'),
        ('ASSESSMENT_VIEWED', 'Assessment Viewed'),
        ('ASSESSMENT_REVIEWED', 'Assessment Reviewed'),
        ('ASSESSMENT_MODIFIED', 'Assessment Modified'),
        ('ASSESSMENT_SENT', 'Assessment Sent'),
        ('CONVERSATION_STARTED', 'Conversation Started'),
        ('CONVERSATION_CLOSED', 'Conversation Closed'),
        ('ESCALATION_TRIGGERED', 'Escalation Triggered'),
        ('CLINICIAN_ASSIGNED', 'Clinician Assigned'),
        ('PATIENT_TRANSFERRED', 'Patient Transferred'),
    ]
    
    STATUS_CHOICES = [
        ('SUCCESS', 'Success'),
        ('FAILURE', 'Failure'),
        ('PARTIAL', 'Partial'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='audit_logs'
    )
    action_type = models.CharField(max_length=30, choices=ACTION_TYPE_CHOICES, db_index=True)
    resource_type = models.CharField(max_length=50, db_index=True)
    resource_id = models.CharField(max_length=100, blank=True, db_index=True)
    description = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    changes = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SUCCESS')
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action_type', 'timestamp']),
            models.Index(fields=['resource_type', 'resource_id']),
        ]
    
    def __str__(self):
        user_str = self.user.phone_number if self.user else 'System'
        return f"{user_str} - {self.action_type} - {self.resource_type}"


class ServiceFailureLog(models.Model):
    """Log service failures for monitoring."""
    
    SERVICE_TYPE_CHOICES = [
        ('GROQ_AI', 'Groq AI'),
        ('TWILIO_API', 'Twilio API'),
        ('DATABASE', 'Database'),
        ('EMAIL', 'Email Service'),
        ('NOTIFICATION', 'Notification Service'),
        ('REDIS', 'Redis Cache'),
        ('AUTHENTICATION', 'Authentication'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service_type = models.CharField(max_length=30, choices=SERVICE_TYPE_CHOICES, db_index=True)
    conversation = models.ForeignKey(
        'conversations.ConversationSession', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='service_failures'
    )
    error_message = models.TextField()
    error_code = models.CharField(max_length=50, blank=True)
    stack_trace = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)
    
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['service_type', 'resolved']),
            models.Index(fields=['timestamp']),
        ]
    
    def __str__(self):
        return f"{self.service_type} - {self.error_code} - {self.timestamp}"


class DataAccessLog(models.Model):
    """Track sensitive data access (HIPAA)."""
    
    ACCESS_TYPE_CHOICES = [
        ('READ', 'Read'),
        ('WRITE', 'Write'),
        ('DELETE', 'Delete'),
        ('EXPORT', 'Export'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='data_accesses')
    patient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='data_accessed_by',
        limit_choices_to={'role': 'PATIENT'}
    )
    data_type = models.CharField(max_length=50)
    access_type = models.CharField(max_length=20, choices=ACCESS_TYPE_CHOICES)
    resource_id = models.CharField(max_length=100)
    purpose = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    
    accessed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ['-accessed_at']
        indexes = [
            models.Index(fields=['patient', 'accessed_at']),
            models.Index(fields=['user', 'accessed_at']),
        ]
    
    def __str__(self):
        return f"{self.user.phone_number if self.user else 'Unknown'} accessed {self.data_type}"


class ConsentLog(models.Model):
    """Track patient consent and agreements."""
    
    CONSENT_TYPE_CHOICES = [
        ('TERMS_AND_CONDITIONS', 'Terms and Conditions'),
        ('PRIVACY_POLICY', 'Privacy Policy'),
        ('DATA_PROCESSING', 'Data Processing Consent'),
        ('MARKETING', 'Marketing Consent'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='consents',
        limit_choices_to={'role': 'PATIENT'}
    )
    consent_type = models.CharField(max_length=30, choices=CONSENT_TYPE_CHOICES)
    version = models.CharField(max_length=20, default='1.0')
    given = models.BooleanField(default=False)
    given_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    consent_document_url = models.URLField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['patient', 'consent_type', 'version']
    
    def __str__(self):
        return f"{self.patient.phone_number} - {self.consent_type}"