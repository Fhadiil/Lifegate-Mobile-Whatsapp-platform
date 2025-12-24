from rest_framework import serializers
from apps.conversations.models import ConversationSession, Message, TriageQuestion
from apps.authentication.models import User


class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user info for conversations."""
    
    class Meta:
        model = User
        fields = ['id', 'phone_number', 'first_name', 'last_name', 'role']


class TriageQuestionSerializer(serializers.ModelSerializer):
    """Triage question serializer."""
    
    class Meta:
        model = TriageQuestion
        fields = [
            'id', 'question_text', 'question_type', 'question_order',
            'patient_response', 'response_timestamp', 'response_processed',
            'created_at'
        ]


class MessageSerializer(serializers.ModelSerializer):
    """Message serializer."""
    
    sender = UserBasicSerializer(read_only=True)
    
    class Meta:
        model = Message
        fields = [
            'id', 'sender', 'message_type', 'content', 'media_url',
            'delivery_status', 'created_at', 'delivered_at', 'read_at'
        ]
        read_only_fields = ['id', 'sender', 'created_at', 'delivered_at', 'read_at']


class ConversationSerializer(serializers.ModelSerializer):
    """Conversation serializer."""
    
    patient = UserBasicSerializer(read_only=True)
    assigned_clinician = UserBasicSerializer(read_only=True)
    messages = MessageSerializer(many=True, read_only=True)
    assessment = serializers.SerializerMethodField()
    latest_message = serializers.SerializerMethodField()
    
    class Meta:
        model = ConversationSession
        fields = [
            'id', 'patient', 'assigned_clinician', 'status', 'chief_complaint',
            'is_escalated', 'escalation_reason', 'ai_questions_asked',
            'messages', 'assessment', 'latest_message',
            'created_at', 'triage_completed_at', 'clinician_assigned_at',
            'closed_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'patient', 'assigned_clinician', 'status', 'chief_complaint',
            'is_escalated', 'ai_questions_asked', 'created_at', 'updated_at',
            'triage_completed_at', 'clinician_assigned_at', 'closed_at'
        ]
    
    def get_assessment(self, obj):
        """Get assessment data if available."""
        try:
            assessment = obj.assessment
            return {
                'id': str(assessment.id),
                'status': assessment.status,
                'confidence_score': assessment.confidence_score,
                'generated_at': assessment.generated_at.isoformat()
            }
        except:
            return None
    
    def get_latest_message(self, obj):
        """Get latest message in conversation."""
        latest = obj.messages.last()
        if latest:
            return {
                'id': str(latest.id),
                'sender': latest.sender.phone_number if latest.sender else 'System',
                'content': latest.content[:200],
                'timestamp': latest.created_at.isoformat(),
                'type': latest.message_type
            }
        return None


class ConversationDetailSerializer(serializers.ModelSerializer):
    """Detailed conversation serializer with all data."""
    
    patient = UserBasicSerializer(read_only=True)
    assigned_clinician = UserBasicSerializer(read_only=True)
    messages = MessageSerializer(many=True, read_only=True)
    triage_questions = TriageQuestionSerializer(many=True, read_only=True)
    assessment = serializers.SerializerMethodField()
    
    class Meta:
        model = ConversationSession
        fields = [
            'id', 'patient', 'assigned_clinician', 'status', 'chief_complaint',
            'is_escalated', 'escalation_reason', 'ai_questions_asked',
            'messages', 'triage_questions', 'assessment',
            'created_at', 'triage_completed_at', 'clinician_assigned_at',
            'first_clinician_response_at', 'closed_at', 'updated_at'
        ]
    
    def get_assessment(self, obj):
        """Get full assessment data if available."""
        try:
            from apps.assessments.serializers import AssessmentDetailSerializer
            assessment = obj.assessment
            return AssessmentDetailSerializer(assessment).data
        except:
            return None