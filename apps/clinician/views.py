import logging
from django.utils import timezone
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.authentication.models import User, ClinicianProfile
from apps.conversations.models import ConversationSession, Message
from apps.assessments.models import AIAssessment, AssessmentReview
from apps.clinician.models import ClinicianAvailability, PatientAssignment, ClinicianAction
from apps.escalations.models import EscalationAlert
from apps.audit.models import AuditLog
from .serializers import (
    ClinicianDashboardSerializer, AssessmentDetailSerializer,
    AssessmentReviewSerializer, ClinicianAvailabilitySerializer
)
from integrations.twilio.client import TwilioClient

logger = logging.getLogger('lifegate')


class ClinicianPermission(IsAuthenticated):
    """Verify user is a clinician."""
    
    def has_permission(self, request, view):
        has_auth = super().has_permission(request, view)
        is_clinician = request.user.role == 'CLINICIAN'
        return has_auth and is_clinician


class ClinicianDashboardViewSet(viewsets.ViewSet):
    """Clinician dashboard and assessment management."""
    
    permission_classes = [ClinicianPermission]
    
    def list(self, request):
        """GET /api/v1/clinician/dashboard/ - Overview dashboard."""
        try:
            clinician = request.user
            
            # Get pending assessments
            pending = AIAssessment.objects.filter(
                conversation__assigned_clinician=clinician,
                status__in=['GENERATED', 'PENDING_REVIEW']
            ).select_related('conversation', 'patient')
            
            # Get active conversations
            active_conversations = ConversationSession.objects.filter(
                assigned_clinician=clinician,
                status__in=['DIRECT_MESSAGING', 'AWAITING_PATIENT_RESPONSE']
            )
            
            # Get escalations
            escalations = EscalationAlert.objects.filter(
                conversation__assigned_clinician=clinician,
                alert_status__in=['PENDING', 'ACKNOWLEDGED']
            )
            
            dashboard = {
                'pending_count': pending.count(),
                'active_conversations_count': active_conversations.count(),
                'escalation_count': escalations.count(),
                'assessments': ClinicianDashboardSerializer(
                    pending, many=True, context={'request': request}
                ).data,
                'escalations': [
                    {
                        'id': str(e.id),
                        'patient': e.conversation.patient.phone_number,
                        'message': e.alert_message,
                        'severity': e.alert_severity,
                        'triggered_at': e.triggered_at.isoformat()
                    }
                    for e in escalations
                ]
            }
            
            # Log dashboard access
            AuditLog.objects.create(
                user=clinician,
                action_type='ASSESSMENT_VIEWED',
                resource_type='Dashboard',
                resource_id='',
                description='Clinician accessed dashboard'
            )
            
            return Response(dashboard, status=status.HTTP_200_OK)
        
        except Exception as e:
            logger.error(f"Error in dashboard: {str(e)}")
            return Response(
                {'error': 'Failed to load dashboard'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='queue')
    def queue(self, request):
        """GET /api/v1/clinician/queue/ - Assessment queue."""
        try:
            clinician = request.user
            
            # Get queue sorted by priority
            queue = AIAssessment.objects.filter(
                conversation__assigned_clinician=clinician,
                status__in=['GENERATED', 'PENDING_REVIEW']
            ).order_by('-generated_at')
            
            serializer = ClinicianDashboardSerializer(
                queue, many=True, context={'request': request}
            )
            
            return Response({
                'count': queue.count(),
                'assessments': serializer.data
            })
        
        except Exception as e:
            logger.error(f"Error getting queue: {str(e)}")
            return Response(
                {'error': 'Failed to load queue'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'], url_path=r'assessments/(?P<assessment_id>[^/.]+)')
    def assessment_detail(self, request, pk=None):
        """GET /api/v1/clinician/assessments/{id}/ - Full assessment details."""
        try:
            assessment = AIAssessment.objects.get(
                id=pk,
                conversation__assigned_clinician=request.user
            )
            
            serializer = AssessmentDetailSerializer(assessment, context={'request': request})
            
            # Log access
            AuditLog.objects.create(
                user=request.user,
                action_type='ASSESSMENT_VIEWED',
                resource_type='Assessment',
                resource_id=str(pk),
                description=f"Clinician viewed assessment {pk}"
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error getting assessment: {str(e)}")
            return Response(
                {'error': 'Failed to load assessment'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def review_assessment(self, request, pk=None):
        """POST /api/v1/clinician/assessments/{id}/review/ - Review and approve/modify/reject."""
        try:
            assessment = AIAssessment.objects.get(
                id=pk,
                conversation__assigned_clinician=request.user
            )
            
            action_type = request.data.get('action')  # APPROVED, MODIFIED, REJECTED
            if action_type not in ['APPROVED', 'MODIFIED', 'REJECTED']:
                return Response(
                    {'error': 'Invalid action'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create review record
            review = AssessmentReview.objects.create(
                assessment=assessment,
                clinician=request.user,
                action=action_type,
                clinician_notes=request.data.get('clinician_notes', ''),
                clinician_risk_level=request.data.get('risk_level', 'MODERATE')
            )
            
            # If modified, store modifications
            if action_type == 'MODIFIED':
                review.modified_recommendations = request.data.get('modified_recommendations')
                review.modified_otc_suggestions = request.data.get('modified_otc_suggestions')
                review.modified_monitoring_advice = request.data.get('modified_monitoring_advice')
                review.save()
            
            # Update assessment status
            assessment.status = action_type
            assessment.save()
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='ASSESSMENT_REVIEWED',
                resource_type='Assessment',
                resource_id=str(pk),
                description=f"Clinician {action_type} assessment",
                changes={'action': action_type}
            )
            
            # Log clinician action
            ClinicianAction.objects.create(
                clinician=request.user,
                conversation=assessment.conversation,
                action_type=f'ASSESSMENT_{action_type}',
                action_details={'assessment_id': str(pk)}
            )
            
            serializer = AssessmentReviewSerializer(review)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error reviewing assessment: {str(e)}")
            return Response(
                {'error': 'Failed to review assessment'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def send_to_patient(self, request, pk=None):
        """POST /api/v1/clinician/assessments/{id}/send-to-patient/ - Send assessment to patient."""
        try:
            assessment = AIAssessment.objects.get(
                id=pk,
                conversation__assigned_clinician=request.user
            )
            
            patient = assessment.patient
            conversation = assessment.conversation
            
            # Format message for patient
            message = self._format_assessment_for_patient(assessment, request.user)
            
            # Send via Twilio
            twilio = TwilioClient()
            twilio.send_message(patient.whatsapp_id, message)
            
            # Update assessment status
            assessment.status = 'SENT_TO_PATIENT'
            assessment.sent_to_patient_at = timezone.now()
            assessment.save()
            
            # Update conversation status
            conversation.status = 'DIRECT_MESSAGING'
            conversation.first_clinician_response_at = timezone.now()
            conversation.save()
            
            # Save message record
            Message.objects.create(
                conversation=conversation,
                sender=request.user,
                message_type='CLINICIAN',
                content=message,
                delivery_status='SENT'
            )
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='ASSESSMENT_SENT',
                resource_type='Assessment',
                resource_id=str(pk),
                description=f"Assessment sent to patient {patient.phone_number}"
            )
            
            ClinicianAction.objects.create(
                clinician=request.user,
                conversation=conversation,
                action_type='ASSESSMENT_SENT'
            )
            
            return Response(
                {'message': 'Assessment sent to patient'},
                status=status.HTTP_200_OK
            )
        
        except AIAssessment.DoesNotExist:
            return Response(
                {'error': 'Assessment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error sending assessment: {str(e)}")
            return Response(
                {'error': 'Failed to send assessment'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        """POST /api/v1/clinician/conversations/{id}/message/ - Send direct message to patient."""
        try:
            conversation = ConversationSession.objects.get(
                id=pk,
                assigned_clinician=request.user
            )
            
            message_body = request.data.get('message')
            if not message_body:
                return Response(
                    {'error': 'Message body required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            patient = conversation.patient
            
            # Send via Twilio
            twilio = TwilioClient()
            twilio.send_message(patient.whatsapp_id, message_body)
            
            # Save message
            msg = Message.objects.create(
                conversation=conversation,
                sender=request.user,
                message_type='CLINICIAN',
                content=message_body,
                delivery_status='SENT'
            )
            
            # Log action
            ClinicianAction.objects.create(
                clinician=request.user,
                conversation=conversation,
                action_type='MESSAGE_SENT',
                action_details={'message_id': str(msg.id)}
            )
            
            return Response(
                {'message_id': str(msg.id)},
                status=status.HTTP_201_CREATED
            )
        
        except ConversationSession.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error sending message: {str(e)}")
            return Response(
                {'error': 'Failed to send message'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='availability')
    def update_availability(self, request):
        """POST /api/v1/clinician/availability/ - Update availability status."""
        try:
            clinician = request.user
            availability, _ = ClinicianAvailability.objects.get_or_create(clinician=clinician)
            
            status_val = request.data.get('status')
            if status_val:
                availability.status = status_val
            
            availability.shift_start = request.data.get('shift_start', availability.shift_start)
            availability.shift_end = request.data.get('shift_end', availability.shift_end)
            availability.save()
            
            serializer = ClinicianAvailabilitySerializer(availability)
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        except Exception as e:
            logger.error(f"Error updating availability: {str(e)}")
            return Response(
                {'error': 'Failed to update availability'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def escalations(self, request):
        """GET /api/v1/clinician/escalations/ - Get escalations."""
        try:
            escalations = EscalationAlert.objects.filter(
                conversation__assigned_clinician=request.user,
                alert_status__in=['PENDING', 'ACKNOWLEDGED']
            ).order_by('-triggered_at')
            
            return Response([
                {
                    'id': str(e.id),
                    'conversation_id': str(e.conversation.id),
                    'patient': e.conversation.patient.phone_number,
                    'message': e.alert_message,
                    'severity': e.alert_severity,
                    'status': e.alert_status,
                    'triggered_at': e.triggered_at.isoformat()
                }
                for e in escalations
            ])
        
        except Exception as e:
            logger.error(f"Error getting escalations: {str(e)}")
            return Response(
                {'error': 'Failed to load escalations'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _format_assessment_for_patient(self, assessment, clinician):
        """Format assessment as WhatsApp message."""
        review = assessment.reviews.first()
        if review and review.action == 'MODIFIED':
            final_data = review.get_final_assessment()
        else:
            final_data = {
                'recommendations': assessment.preliminary_recommendations,
                'otc_suggestions': assessment.otc_suggestions,
                'monitoring_advice': assessment.monitoring_advice,
                'clinician_notes': ''
            }
        
        symptoms = assessment.symptoms_overview.get('primary_symptoms', [])
        likely_condition = assessment.key_observations.get('likely_condition', 'Assessment pending')
        medications = final_data['otc_suggestions'].get('medications', [])
        recommendations = final_data['recommendations'].get('lifestyle_changes', [])
        monitoring = final_data['monitoring_advice'].get('when_to_seek_help', [])
        
        message = f"""✅ *Assessment Complete*

Hi {assessment.patient.first_name or 'Patient'},

{clinician.first_name or 'Dr'} {clinician.last_name} has reviewed your assessment.

📋 *YOUR SYMPTOMS:*
"""
        
        for symptom in symptoms[:3]:
            message += f"• {symptom}\n"
        
        message += f"""
💡 *LIKELY CAUSE:*
{likely_condition}

💊 *MEDICATIONS:*
"""
        
        for med in medications[:2]:
            message += f"• {med.get('name')}: {med.get('dosage')} {med.get('frequency')}\n"
        
        message += "\n🎯 *WHAT TO DO:*\n"
        for rec in recommendations[:3]:
            message += f"• {rec}\n"
        
        message += "\n⚠️ *SEEK HELP IF:*\n"
        for item in monitoring[:3]:
            message += f"• {item}\n"
        
        if final_data.get('clinician_notes'):
            message += f"\n👨‍⚕️ *DOCTOR'S NOTE:*\n{final_data['clinician_notes']}"
        
        message += "\n\n[MESSAGE DOCTOR]"
        
        return message