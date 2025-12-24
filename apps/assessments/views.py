import logging
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.assessments.models import AIAssessment, AssessmentReview
from apps.conversations.models import ConversationSession
from apps.audit.models import AuditLog
from .serializers import AssessmentSerializer, AssessmentDetailSerializer, AssessmentReviewSerializer

logger = logging.getLogger('lifegate')


class PatientPermission(IsAuthenticated):
    """Verify user is authenticated."""
    
    def has_permission(self, request, view):
        return super().has_permission(request, view)


class AssessmentViewSet(viewsets.ModelViewSet):
    """Assessment management endpoints."""
    
    permission_classes = [PatientPermission]
    
    def get_queryset(self):
        """Get assessments accessible to user."""
        user = self.request.user
        
        if user.role == 'PATIENT':
            # Patients see their own assessments
            return AIAssessment.objects.filter(patient=user).select_related(
                'patient', 'conversation'
            )
        elif user.role == 'CLINICIAN':
            # Clinicians see assessments assigned to them
            return AIAssessment.objects.filter(
                conversation__assigned_clinician=user
            ).select_related('patient', 'conversation')
        else:
            return AIAssessment.objects.none()
    
    def list(self, request):
        """GET /api/v1/assessments/ - List assessments."""
        try:
            assessments = self.get_queryset().order_by('-generated_at')
            
            # Filter by status if provided
            status_filter = request.query_params.get('status')
            if status_filter:
                assessments = assessments.filter(status=status_filter)
            
            serializer = AssessmentSerializer(
                assessments, many=True, context={'request': request}
            )
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='ASSESSMENT_VIEWED',
                resource_type='AIAssessment',
                resource_id='',
                description=f"User viewed assessments list (count: {assessments.count()})"
            )
            
            return Response({
                'count': assessments.count(),
                'assessments': serializer.data
            }, status=status.HTTP_200_OK)
        
        except Exception as e:
            logger.error(f"Error listing assessments: {str(e)}")
            return Response(
                {'error': 'Failed to load assessments'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def retrieve(self, request, pk=None):
        """GET /api/v1/assessments/{id}/ - Get assessment details."""
        try:
            assessment = self.get_queryset().get(id=pk)
            
            # Verify access
            if request.user.role == 'PATIENT' and assessment.patient != request.user:
                return Response(
                    {'error': 'Access denied'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            serializer = AssessmentDetailSerializer(
                assessment, context={'request': request}
            )
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='ASSESSMENT_VIEWED',
                resource_type='AIAssessment',
                resource_id=str(pk),
                description=f"User viewed assessment {pk}"
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error retrieving assessment: {str(e)}")
            return Response(
                {'error': 'Failed to load assessment'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        """POST /api/v1/assessments/{id}/acknowledge/ - Patient acknowledges assessment."""
        try:
            assessment = self.get_queryset().get(id=pk)
            
            # Only patients can acknowledge
            if request.user.role != 'PATIENT':
                return Response(
                    {'error': 'Only patients can acknowledge assessments'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            if assessment.patient != request.user:
                return Response(
                    {'error': 'Access denied'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Mark as acknowledged (optional - could add acknowledged_at field)
            # For now, we just log the action
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='ASSESSMENT_REVIEWED',
                resource_type='AIAssessment',
                resource_id=str(pk),
                description=f"Patient acknowledged assessment {pk}"
            )
            
            return Response(
                {'status': 'Assessment acknowledged'},
                status=status.HTTP_200_OK
            )
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error acknowledging assessment: {str(e)}")
            return Response(
                {'error': 'Failed to acknowledge assessment'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def reviews(self, request, pk=None):
        """GET /api/v1/assessments/{id}/reviews/ - Get assessment reviews."""
        try:
            assessment = self.get_queryset().get(id=pk)
            
            reviews = assessment.reviews.all().order_by('-review_completed_at')
            
            serializer = AssessmentReviewSerializer(reviews, many=True)
            
            return Response({
                'assessment_id': str(pk),
                'reviews_count': reviews.count(),
                'reviews': serializer.data
            }, status=status.HTTP_200_OK)
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error getting reviews: {str(e)}")
            return Response(
                {'error': 'Failed to load reviews'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def summary(self, request, pk=None):
        """GET /api/v1/assessments/{id}/summary/ - Get assessment summary for patient."""
        try:
            assessment = self.get_queryset().get(id=pk)
            
            if request.user.role == 'PATIENT' and assessment.patient != request.user:
                return Response(
                    {'error': 'Access denied'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Get latest review if exists
            latest_review = assessment.reviews.order_by('-review_completed_at').first()
            
            summary = {
                'id': str(assessment.id),
                'chief_complaint': assessment.chief_complaint,
                'status': assessment.status,
                'confidence': f"{int(assessment.confidence_score * 100)}%",
                'likely_condition': assessment.key_observations.get('likely_condition'),
                'symptoms': assessment.symptoms_overview.get('primary_symptoms', [])[:5],
                'recommendations': assessment.preliminary_recommendations.get('lifestyle_changes', [])[:5],
                'medications': assessment.otc_suggestions.get('medications', [])[:3],
                'when_to_seek_help': assessment.monitoring_advice.get('when_to_seek_help', [])[:3],
                'clinician_notes': latest_review.clinician_notes if latest_review else '',
                'risk_level': latest_review.clinician_risk_level if latest_review else 'MODERATE',
                'generated_at': assessment.generated_at.isoformat(),
                'sent_at': assessment.sent_to_patient_at.isoformat() if assessment.sent_to_patient_at else None
            }
            
            return Response(summary, status=status.HTTP_200_OK)
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error getting summary: {str(e)}")
            return Response(
                {'error': 'Failed to load summary'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def request_follow_up(self, request, pk=None):
        """POST /api/v1/assessments/{id}/request-follow-up/ - Patient requests follow-up."""
        try:
            assessment = self.get_queryset().get(id=pk)
            
            if request.user.role != 'PATIENT':
                return Response(
                    {'error': 'Only patients can request follow-ups'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            if assessment.patient != request.user:
                return Response(
                    {'error': 'Access denied'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            reason = request.data.get('reason', '')
            
            # Create a new conversation for follow-up
            conversation = ConversationSession.objects.create(
                patient=request.user,
                assigned_clinician=assessment.conversation.assigned_clinician,
                status='PENDING_CLINICIAN_REVIEW',
                chief_complaint=f"Follow-up: {reason or 'Patient requested follow-up'}"
            )
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='ASSESSMENT_REVIEWED',
                resource_type='ConversationSession',
                resource_id=str(conversation.id),
                description=f"Patient requested follow-up for assessment {pk}"
            )
            
            return Response({
                'status': 'Follow-up requested',
                'conversation_id': str(conversation.id)
            }, status=status.HTTP_201_CREATED)
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error requesting follow-up: {str(e)}")
            return Response(
                {'error': 'Failed to request follow-up'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def compliance_summary(self, request, pk=None):
        """GET /api/v1/assessments/{id}/compliance/ - Get HIPAA compliance summary."""
        try:
            assessment = self.get_queryset().get(id=pk)
            
            # Get audit logs for this assessment
            from apps.audit.models import AuditLog
            audit_logs = AuditLog.objects.filter(
                resource_type='AIAssessment',
                resource_id=str(pk)
            ).order_by('-timestamp')
            
            summary = {
                'assessment_id': str(assessment.id),
                'patient_id': str(assessment.patient.id),
                'created_at': assessment.generated_at.isoformat(),
                'access_count': audit_logs.count(),
                'accessed_by': list(set([
                    log.user.phone_number for log in audit_logs if log.user
                ])),
                'last_accessed': audit_logs.first().timestamp.isoformat() if audit_logs.exists() else None,
                'status': assessment.status
            }
            
            return Response(summary, status=status.HTTP_200_OK)
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error getting compliance summary: {str(e)}")
            return Response(
                {'error': 'Failed to load compliance data'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )