from rest_framework import serializers
from django.contrib.auth import authenticate
from apps.authentication.models import User, PatientProfile, ClinicianProfile


class UserLoginSerializer(serializers.Serializer):
    """Login serializer."""
    
    phone_number = serializers.CharField()
    password = serializers.CharField(write_only=True)
    
    def validate(self, attrs):
        phone_number = attrs.get('phone_number')
        password = attrs.get('password')
        
        try:
            user = User.objects.get(phone_number=phone_number)
            if not user.check_password(password):
                raise serializers.ValidationError("Invalid credentials")
            attrs['user'] = user
        except User.DoesNotExist:
            raise serializers.ValidationError("Invalid credentials")
        
        return attrs
    
    def create(self, validated_data):
        return validated_data['user']


class PatientProfileSerializer(serializers.ModelSerializer):
    """Patient profile serializer."""
    
    class Meta:
        model = PatientProfile
        fields = [
            'id', 'date_of_birth', 'age', 'gender', 'medical_history',
            'current_medications', 'allergies', 'emergency_contact_name',
            'emergency_contact_phone', 'preferred_language', 'total_consultations'
        ]


class ClinicianProfileSerializer(serializers.ModelSerializer):
    """Clinician profile serializer."""
    
    class Meta:
        model = ClinicianProfile
        fields = [
            'id', 'license_number', 'license_expiry', 'specialization',
            'hospital_affiliation', 'is_available', 'max_concurrent_patients',
            'response_sla_hours', 'total_patients_handled', 'total_assessments_reviewed',
            'avg_review_time_minutes'
        ]
        read_only_fields = [
            'total_patients_handled', 'total_assessments_reviewed',
            'avg_review_time_minutes'
        ]


class UserSerializer(serializers.ModelSerializer):
    """User serializer."""
    
    patient_profile = PatientProfileSerializer(read_only=True)
    clinician_profile = ClinicianProfileSerializer(read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id', 'username', 'phone_number', 'whatsapp_id', 'first_name',
            'last_name', 'email', 'role', 'is_active', 'patient_profile',
            'clinician_profile', 'terms_accepted', 'created_at'
        ]
        read_only_fields = ['id', 'username', 'whatsapp_id', 'created_at']
    
    def create(self, validated_data):
        password = validated_data.pop('password', None)
        instance = self.Meta.model(**validated_data)
        if password is not None:
            instance.set_password(password)
        instance.save()
        return instance