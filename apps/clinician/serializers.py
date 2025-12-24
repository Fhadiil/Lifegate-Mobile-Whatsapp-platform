from rest_framework import serializers
from apps.authentication.models import User, PatientProfile
from apps.conversations.models import ConversationSession, Message
from apps.assessments.models import AIAssessment, AssessmentReview
from apps.clinician.models import ClinicianAvailability


class PatientSummarySerializer(serializers.ModelSerializer):
    """Basic patient info for clinician dashboard."""
    
    age = serializers.SerializerMethodField()
    gender = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'phone_number', 'first_name', 'last_name', 'age', 'gender']
    
    def get_age(self, obj):
        return obj.patient_profile.age if hasattr(obj, 'patient_profile') else None
    
    def get_gender(self, obj):
        return obj.patient_profile.gender if hasattr(obj, 'patient_profile') else None


class ClinicianDashboardSerializer(serializers.ModelSerializer):
    """Assessment summary for clinician dashboard."""
    
    patient = PatientSummarySerializer(read_only=True)
    severity = serializers.SerializerMethodField()
    
    class Meta:
        model = AIAssessment
        fields = [
            'id', 'patient', 'chief_complaint', 'severity', 
            'confidence_score', 'status', 'generated_at'
        ]
    
    def get_severity(self, obj):
        severity = obj.symptoms_overview.get('severity_rating', 5)
        if severity >= 8:
            return 'HIGH'
        elif severity >= 5:
            return 'MODERATE'
        else:
            return 'LOW'


class AssessmentDetailSerializer(serializers.ModelSerializer):
    """Full assessment details for clinician review."""
    
    patient = PatientSummarySerializer(read_only=True)
    conversation = serializers.SerializerMethodField()
    messages = serializers.SerializerMethodField()
    
    class Meta:
        model = AIAssessment
        fields = [
            'id', 'patient', 'chief_complaint', 'conversation',
            'symptoms_overview', 'key_observations', 
            'preliminary_recommendations', 'otc_suggestions',
            'monitoring_advice', 'red_flags_detected',
            'confidence_score', 'status', 'messages'
        ]
    
    def get_conversation(self, obj):
        return str(obj.conversation.id)
    
    def get_messages(self, obj):
        messages = obj.conversation.messages.all()
        return [
            {
                'sender': msg.sender.phone_number if msg.sender else 'System',
                'content': msg.content[:200],
                'timestamp': msg.created_at.isoformat(),
                'type': msg.message_type
            }
            for msg in messages[-5:]  # Last 5 messages
        ]


class AssessmentReviewSerializer(serializers.ModelSerializer):
    """Assessment review record."""
    
    clinician_name = serializers.SerializerMethodField()
    
    class Meta:
        model = AssessmentReview
        fields = [
            'id', 'assessment', 'clinician_name', 'action',
            'clinician_notes', 'clinician_risk_level',
            'requires_urgent_follow_up', 'follow_up_days',
            'review_completed_at'
        ]
    
    def get_clinician_name(self, obj):
        return f"{obj.clinician.first_name} {obj.clinician.last_name}".strip()


class ClinicianAvailabilitySerializer(serializers.ModelSerializer):
    """Clinician availability status."""
    
    class Meta:
        model = ClinicianAvailability
        fields = [
            'id', 'status', 'shift_start', 'shift_end',
            'current_patient_count', 'last_activity'
        ]
        read_only_fields = ['current_patient_count', 'last_activity']


class ConversationSummarySerializer(serializers.ModelSerializer):
    """Conversation summary for clinician."""
    
    patient_name = serializers.SerializerMethodField()
    latest_message = serializers.SerializerMethodField()
    
    class Meta:
        model = ConversationSession
        fields = [
            'id', 'patient_name', 'chief_complaint', 'status',
            'latest_message', 'updated_at'
        ]
    
    def get_patient_name(self, obj):
        return obj.patient.get_full_name() or obj.patient.phone_number
    
    def get_latest_message(self, obj):
        latest = obj.messages.last()
        if latest:
            return {
                'content': latest.content[:100],
                'timestamp': latest.created_at.isoformat(),
                'from': latest.sender.phone_number if latest.sender else 'System'
            }
        return None


class MessageSerializer(serializers.ModelSerializer):
    """Message detail."""
    
    sender_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Message
        fields = [
            'id', 'sender_name', 'content', 'message_type',
            'created_at', 'delivery_status'
        ]
    
    def get_sender_name(self, obj):
        if obj.sender:
            return obj.sender.get_full_name() or obj.sender.phone_number
        return 'System'