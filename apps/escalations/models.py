import uuid
from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.utils import timezone
from apps.authentication.models import User
from apps.conversations.models import ConversationSession


class EscalationRule(models.Model):
    """Define escalation triggers and actions."""
    
    TRIGGER_TYPE_CHOICES = [
        ('RED_FLAG_SYMPTOM', 'Red Flag Symptom'),
        ('SEVERITY_THRESHOLD', 'Severity Threshold'),
        ('RESPONSE_SLA_BREACH', 'Response SLA Breach'),
        ('CLINICIAN_UNAVAILABLE', 'Clinician Unavailable'),
        ('MANUAL_REQUEST', 'Manual Request'),
        ('PATIENT_FOLLOW_UP', 'Patient Follow-up Required'),
    ]
    
    ACTION_CHOICES = [
        ('IMMEDIATE_ALERT', 'Immediate Alert'),
        ('QUEUE_PRIORITY', 'Queue Priority'),
        ('SUPERVISOR_ALERT', 'Supervisor Alert'),
        ('EMERGENCY_PROTOCOL', 'Emergency Protocol'),
        ('AUTO_TRANSFER', 'Auto Transfer'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    trigger_type = models.CharField(max_length=30, choices=TRIGGER_TYPE_CHOICES)
    trigger_keywords = models.JSONField(default=list, blank=True)
    severity_threshold = models.IntegerField(null=True, blank=True)
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    action_recipients = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    priority_level = models.IntegerField(default=1)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-priority_level', 'name']
    
    def __str__(self):
        return self.name


class EscalationAlert(models.Model):
    """Escalation alert instance."""
    
    ALERT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ACKNOWLEDGED', 'Acknowledged'),
        ('HANDLED', 'Handled'),
        ('DISMISSED', 'Dismissed'),
        ('EXPIRED', 'Expired'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        ConversationSession, on_delete=models.CASCADE, related_name='escalation_alerts'
    )
    rule = models.ForeignKey(
        EscalationRule, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='alerts'
    )
    alert_status = models.CharField(
        max_length=20, choices=ALERT_STATUS_CHOICES, default='PENDING',
        db_index=True
    )
    alert_message = models.TextField()
    alert_severity = models.CharField(
        max_length=20,
        choices=[('LOW', 'Low'), ('MEDIUM', 'Medium'), ('HIGH', 'High'), ('CRITICAL', 'Critical')],
        default='MEDIUM'
    )
    triggered_by_keyword = models.CharField(max_length=200, blank=True)
    
    triggered_at = models.DateTimeField(auto_now_add=True, db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='handled_escalations'
    )
    resolution_notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-triggered_at']
        indexes = [
            models.Index(fields=['alert_status', 'triggered_at']),
            models.Index(fields=['conversation', 'alert_status']),
        ]
    
    def __str__(self):
        return f"Alert {self.id} - {self.alert_status}"
    
    def mark_acknowledged(self, by_user=None):
        """Mark alert as acknowledged."""
        self.alert_status = 'ACKNOWLEDGED'
        self.acknowledged_at = timezone.now()
        if by_user:
            self.handled_by = by_user
        self.save()
    
    def mark_handled(self, by_user=None, notes=''):
        """Mark alert as handled."""
        self.alert_status = 'HANDLED'
        self.handled_at = timezone.now()
        self.resolution_notes = notes
        if by_user:
            self.handled_by = by_user
        self.save()


class EscalationHistory(models.Model):
    """Track escalation history for audit."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        ConversationSession, on_delete=models.CASCADE, related_name='escalation_history'
    )
    escalation_alert = models.ForeignKey(
        EscalationAlert, on_delete=models.CASCADE, related_name='history'
    )
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='escalation_changes'
    )
    reason = models.TextField(blank=True)
    
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"History {self.id} - {self.new_status}"