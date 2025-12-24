import logging
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from apps.authentication.models import User, PatientProfile, ClinicianProfile
from apps.audit.models import AuditLog, ConsentLog
from .serializers import (
    UserLoginSerializer, UserSerializer, PatientProfileSerializer,
    ClinicianProfileSerializer
)

logger = logging.getLogger('lifegate')


class AuthViewSet(viewsets.ViewSet):
    """Authentication endpoints."""
    
    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def login(self, request):
        """POST /api/v1/auth/login/ - User login."""
        serializer = UserLoginSerializer(data=request.data)
        
        if serializer.is_valid():
            user = serializer.validated_data['user']
            
            refresh = RefreshToken.for_user(user)
            
            # Update last activity
            user.last_activity = timezone.now()
            user.save()
            
            # Log action
            AuditLog.objects.create(
                user=user,
                action_type='USER_LOGIN',
                resource_type='User',
                resource_id=str(user.id),
                description=f"User {user.phone_number} logged in",
                ip_address=self._get_client_ip(request),
                status='SUCCESS'
            )
            
            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': UserSerializer(user).data
            }, status=status.HTTP_200_OK)
        
        # Log failed attempt
        AuditLog.objects.create(
            action_type='USER_LOGIN',
            resource_type='User',
            resource_id='',
            description='Failed login attempt',
            ip_address=self._get_client_ip(request),
            status='FAILURE'
        )
        
        return Response(
            {'error': 'Invalid credentials'},
            status=status.HTTP_401_UNAUTHORIZED
        )
    
    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def register(self, request):
        """POST /api/v1/auth/register/ - User registration."""
        serializer = UserSerializer(data=request.data)
        
        if serializer.is_valid():
            user = serializer.save()
            
            # Create profile based on role
            if user.role == 'PATIENT':
                PatientProfile.objects.create(user=user)
            elif user.role == 'CLINICIAN':
                ClinicianProfile.objects.create(user=user, license_number=request.data.get('license_number'))
            
            # Accept terms
            ConsentLog.objects.create(
                patient=user if user.role == 'PATIENT' else None,
                consent_type='TERMS_AND_CONDITIONS',
                given=True,
                given_at=timezone.now(),
                ip_address=self._get_client_ip(request)
            )
            
            # Log action
            AuditLog.objects.create(
                user=user,
                action_type='USER_CREATED',
                resource_type='User',
                resource_id=str(user.id),
                description=f"New user registered: {user.phone_number}",
                ip_address=self._get_client_ip(request),
                status='SUCCESS'
            )
            
            refresh = RefreshToken.for_user(user)
            
            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': UserSerializer(user).data
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    
    def clinician_register(self, request):
        """Register clinician with WhatsApp support"""
        
        phone_number = request.data.get('phone_number')
        whatsapp_number = request.data.get('whatsapp_number', phone_number)
        
        # Create user
        user = User.objects.create_user(
            username=phone_number.replace('+', ''),
            phone_number=phone_number,
            whatsapp_id=f"whatsapp:{whatsapp_number}",
            role='CLINICIAN'
        )
        
        # Create clinician profile
        ClinicianProfile.objects.create(
            user=user,
            license_number=request.data.get('license_number'),
            specialization=request.data.get('specialization')
        )
        
        return Response({'status': 'Clinician registered'})
    
    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated])
    def logout(self, request):
        """POST /api/v1/auth/logout/ - User logout."""
        AuditLog.objects.create(
            user=request.user,
            action_type='USER_LOGOUT',
            resource_type='User',
            resource_id=str(request.user.id),
            description=f"User {request.user.phone_number} logged out",
            status='SUCCESS'
        )
        
        return Response({'status': 'logged out'}, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        """GET /api/v1/auth/me/ - Get current user."""
        return Response(UserSerializer(request.user).data)
    
    @action(detail=False, methods=['get', 'put'], permission_classes=[IsAuthenticated])
    def patient_profile(self, request):
        """GET/PUT /api/v1/auth/patient-profile/ - Patient profile."""
        if request.user.role != 'PATIENT':
            return Response(
                {'error': 'Not a patient'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            profile = request.user.patient_profile
        except PatientProfile.DoesNotExist:
            profile = PatientProfile.objects.create(user=request.user)
        
        if request.method == 'GET':
            serializer = PatientProfileSerializer(profile)
            return Response(serializer.data)
        
        elif request.method == 'PUT':
            serializer = PatientProfileSerializer(profile, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                
                AuditLog.objects.create(
                    user=request.user,
                    action_type='PROFILE_VIEWED',
                    resource_type='PatientProfile',
                    resource_id=str(profile.id),
                    description='Patient updated profile',
                    changes=serializer.validated_data
                )
                
                return Response(serializer.data)
            
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def clinician_profile(self, request):
        """GET /api/v1/auth/clinician-profile/ - Clinician profile."""
        if request.user.role != 'CLINICIAN':
            return Response(
                {'error': 'Not a clinician'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            profile = request.user.clinician_profile
            serializer = ClinicianProfileSerializer(profile)
            return Response(serializer.data)
        except ClinicianProfile.DoesNotExist:
            return Response(
                {'error': 'Profile not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @staticmethod
    def _get_client_ip(request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip