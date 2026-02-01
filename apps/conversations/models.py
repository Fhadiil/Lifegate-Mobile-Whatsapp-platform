import uuid
from django.db import models
from django.contrib.postgres.fields import ArrayField
from apps.authentication.models import User


class ConversationSession(models.Model):
    """Main conversation session between patient and system/clinician."""
    
    MODE_CHOICES = [
        ('UNDECIDED', 'Mode Not Selected'),
        ('AI_ONLY', 'AI Only Chat'),
        ('CLINICIAN', 'Clinician Consultation')
    ]
    
    STATUS_CHOICES = [
        ('INITIAL', 'Initial'),
        ('AWAITING_ACCEPTANCE', 'Awaiting User Agreement'),
        ('MODE_SELECTION', 'Choosing AI or Clinician'),
        ('AI_ONLY_ACTIVE', 'AI Only - Active Chat'),
        ('ESCALATION_CONSENT', 'Requesting Clinician Consent'),
        ('AWAITING_PATIENT_PROFILE', 'Awaiting Profile Info'),
        ('AI_TRIAGE_IN_PROGRESS', 'AI Triage In Progress'),
        ('AI_ASSESSMENT_GENERATED', 'Assessment Generated'),
        ('PENDING_CLINICIAN_REVIEW', 'Pending Clinician Review'),
        ('CLINICIAN_OVERRIDE', 'Clinician Override'),
        ('AWAITING_PATIENT_RESPONSE', 'Awaiting Patient Response'),
        ('DIRECT_MESSAGING', 'Direct Messaging'),
        ('COMPLETED', 'Completed'),
        ('CLOSED', 'Closed'),
        ('ESCALATED', 'Escalated'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversation_sessions')
    assigned_clinician = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_conversations', limit_choices_to={'role': 'CLINICIAN'}
    )
    
    mode = models.CharField(
        max_length=20, 
        choices=MODE_CHOICES, 
        default='UNDECIDED',
        db_index=True
    )
    
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default='INITIAL',
        db_index=True
    )
    
    chief_complaint = models.TextField(blank=True)
    is_escalated = models.BooleanField(default=False)
    escalation_reason = models.CharField(max_length=200, blank=True)
    ai_questions_asked = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    triage_completed_at = models.DateTimeField(null=True, blank=True)
    clinician_assigned_at = models.DateTimeField(null=True, blank=True)
    first_clinician_response_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_paid = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['patient', 'status']),
            models.Index(fields=['assigned_clinician', 'status']),
            models.Index(fields=['is_escalated']),
            models.Index(fields=['mode']),
        ]
    
    def __str__(self):
        return f"Conversation {self.id} - {self.patient.phone_number} - {self.status}"
    
    def is_active(self):
        """Check if conversation is still active."""
        return self.status not in ['CLOSED', 'ESCALATED', 'COMPLETED']


class Message(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ('PATIENT', 'Patient Message'),
        ('AI_QUERY', 'AI Question'),
        ('AI_RESPONSE', 'AI Response'),
        ('SYSTEM', 'System Message'),
        ('CLINICIAN', 'Clinician Response'),
        ('ESCALATION_ALERT', 'Escalation Alert'),
    ]
    
    DELIVERY_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('DELIVERED', 'Delivered'),
        ('READ', 'Read'),
        ('FAILED', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    conversation = models.ForeignKey(
        ConversationSession,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    
    sender = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='sent_messages'
    )
    
    message_type = models.CharField(
        max_length=20,
        choices=MESSAGE_TYPE_CHOICES
    )
    
    content = models.TextField()
    
    media_url = models.URLField(blank=True, null=True)
    media_type = models.CharField(max_length=50, blank=True, null=True)
    
    delivery_status = models.CharField(
        max_length=20,
        choices=DELIVERY_STATUS_CHOICES,
        default='PENDING'
    )
    
    twilio_message_sid = models.CharField(max_length=100, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
            models.Index(fields=['sender', 'created_at']),
        ]

    
    def __str__(self):
        return f"Message from {self.sender.phone_number if self.sender else 'System'} - {self.created_at}"


class TriageQuestion(models.Model):
    """AI-generated triage questions."""
    
    QUESTION_TYPE_CHOICES = [
        ('OPEN_ENDED', 'Open Ended'),
        ('YES_NO', 'Yes/No'),
        ('MULTIPLE_CHOICE', 'Multiple Choice'),
        ('SEVERITY', 'Severity Scale'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        ConversationSession, on_delete=models.CASCADE, related_name='triage_questions'
    )
    question_text = models.TextField()
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPE_CHOICES)
    question_order = models.IntegerField()
    patient_response = models.TextField(blank=True)
    response_timestamp = models.DateTimeField(null=True, blank=True)
    response_processed = models.BooleanField(default=False)
    ai_generated_next_question = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['question_order']
        indexes = [
            models.Index(fields=['conversation', 'question_order']),
        ]
    
    def __str__(self):
        return f"Q{self.question_order} for {self.conversation.id}"
