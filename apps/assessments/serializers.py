from rest_framework import serializers
from apps.assessments.models import AIAssessment, AssessmentReview
from apps.authentication.models import User


class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user info."""
    
    class Meta:
        model = User
        fields = ['id', 'phone_number', 'first_name', 'last_name', 'role']


class AssessmentReviewSerializer(serializers.ModelSerializer):
    """Assessment review serializer."""
    
    clinician_name = serializers.SerializerMethodField()
    
    class Meta:
        model = AssessmentReview
        fields = [
            'id', 'clinician_name', 'action', 'clinician_notes',
            'clinician_risk_level', 'requires_urgent_follow_up',
            'follow_up_days', 'review_completed_at'
        ]
    
    def get_clinician_name(self, obj):
        if obj.clinician:
            return f"{obj.clinician.first_name} {obj.clinician.last_name}".strip()
        return "Unknown"


class AssessmentSerializer(serializers.ModelSerializer):
    """Assessment summary serializer."""
    
    patient_name = serializers.SerializerMethodField()
    clinician_name = serializers.SerializerMethodField()
    severity = serializers.SerializerMethodField()
    
    class Meta:
        model = AIAssessment
        fields = [
            'id', 'patient_name', 'clinician_name', 'chief_complaint',
            'severity', 'confidence_score', 'status', 'generated_at'
        ]
    
    def get_patient_name(self, obj):
        return obj.patient.get_full_name() or obj.patient.phone_number
    
    def get_clinician_name(self, obj):
        if obj.conversation.assigned_clinician:
            clinician = obj.conversation.assigned_clinician
            return f"{clinician.first_name} {clinician.last_name}".strip()
        return "Unassigned"
    
    def get_severity(self, obj):
        severity = obj.symptoms_overview.get('severity_rating', 5)
        if severity >= 8:
            return 'HIGH'
        elif severity >= 5:
            return 'MODERATE'
        else:
            return 'LOW'


class AssessmentDetailSerializer(serializers.ModelSerializer):
    """Full assessment details serializer."""
    
    patient = UserBasicSerializer(read_only=True)
    clinician = serializers.SerializerMethodField()
    reviews = AssessmentReviewSerializer(many=True, read_only=True)
    
    class Meta:
        model = AIAssessment
        fields = [
            'id', 'patient', 'clinician', 'patient_age', 'patient_gender',
            'chief_complaint', 'symptoms_overview', 'key_observations',
            'preliminary_recommendations', 'otc_suggestions', 'monitoring_advice',
            'red_flags_detected', 'confidence_score', 'status', 'assessment_notes',
            'reviews', 'generated_at', 'sent_to_patient_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'patient', 'clinician', 'patient_age', 'patient_gender',
            'chief_complaint', 'symptoms_overview', 'key_observations',
            'preliminary_recommendations', 'otc_suggestions', 'monitoring_advice',
            'red_flags_detected', 'confidence_score', 'reviews', 'generated_at'
        ]
    
    def get_clinician(self, obj):
        if obj.conversation.assigned_clinician:
            clinician = obj.conversation.assigned_clinician
            return {
                'id': str(clinician.id),
                'name': f"{clinician.first_name} {clinician.last_name}".strip(),
                'phone': clinician.phone_number
            }
        return None


class AssessmentForClinicianSerializer(serializers.ModelSerializer):
    """Assessment serializer optimized for clinician view."""
    
    patient_info = serializers.SerializerMethodField()
    last_review = serializers.SerializerMethodField()
    
    class Meta:
        model = AIAssessment
        fields = [
            'id', 'patient_info', 'chief_complaint', 'symptoms_overview',
            'key_observations', 'preliminary_recommendations', 'otc_suggestions',
            'monitoring_advice', 'red_flags_detected', 'confidence_score',
            'status', 'assessment_notes', 'last_review', 'generated_at'
        ]
    
    def get_patient_info(self, obj):
        return {
            'name': obj.patient.get_full_name() or obj.patient.phone_number,
            'phone': obj.patient.phone_number,
            'age': obj.patient_age,
            'gender': obj.patient_gender
        }
    
    def get_last_review(self, obj):
        last_review = obj.reviews.order_by('-review_completed_at').first()
        if last_review:
            return AssessmentReviewSerializer(last_review).data
        return None


class AssessmentForPatientSerializer(serializers.ModelSerializer):
    """Assessment serializer optimized for patient view (patient-friendly)."""
    
    clinician_name = serializers.SerializerMethodField()
    recommendations_summary = serializers.SerializerMethodField()
    
    class Meta:
        model = AIAssessment
        fields = [
            'id', 'chief_complaint', 'clinician_name', 'recommendations_summary',
            'status', 'confidence_score', 'sent_to_patient_at'
        ]
    
    def get_clinician_name(self, obj):
        if obj.conversation.assigned_clinician:
            clinician = obj.conversation.assigned_clinician
            return f"Dr. {clinician.last_name}"
        return "Your Healthcare Provider"
    
    def get_recommendations_summary(self, obj):
        review = obj.reviews.filter(action__in=['APPROVED', 'MODIFIED']).order_by('-review_completed_at').first()
        
        if review and review.action == 'MODIFIED':
            data = {
                'recommendations': review.modified_recommendations or obj.preliminary_recommendations,
                'medications': review.modified_otc_suggestions or obj.otc_suggestions,
                'monitoring': review.modified_monitoring_advice or obj.monitoring_advice,
            }
        else:
            data = {
                'recommendations': obj.preliminary_recommendations,
                'medications': obj.otc_suggestions,
                'monitoring': obj.monitoring_advice,
            }
        
        return {
            'lifestyle_changes': data['recommendations'].get('lifestyle_changes', [])[:5],
            'medications': data['medications'].get('medications', [])[:3],
            'when_to_seek_help': data['monitoring'].get('when_to_seek_help', [])[:3],
        }