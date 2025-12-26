import logging
from django.utils import timezone
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.authentication.models import User
from apps.conversations.models import ConversationSession, Message, TriageQuestion
from apps.assessments.models import AIAssessment
from apps.audit.models import AuditLog
from integrations.twilio.client import TwilioClient
from .serializers import (
    ConversationSerializer, MessageSerializer, TriageQuestionSerializer
)

logger = logging.getLogger('lifegate')


class PatientPermission(IsAuthenticated):
    """Verify user is authenticated."""
    
    def has_permission(self, request, view):
        return super().has_permission(request, view)


class ConversationViewSet(viewsets.ModelViewSet):
    """Patient conversation management."""
    
    permission_classes = [PatientPermission]
    serializer_class = ConversationSerializer
    
    def get_queryset(self):
        """Get conversations for current user."""
        user = self.request.user
        
        if user.role == 'PATIENT':
            return ConversationSession.objects.filter(patient=user)
        elif user.role == 'CLINICIAN':
            return ConversationSession.objects.filter(assigned_clinician=user)
        else:
            return ConversationSession.objects.none()
    
    def list(self, request):
        """GET /api/v1/conversations/ - List user's conversations."""
        try:
            conversations = self.get_queryset().select_related(
                'patient', 'assigned_clinician'
            ).order_by('-updated_at')
            
            serializer = ConversationSerializer(
                conversations, many=True, context={'request': request}
            )
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='CONVERSATION_STARTED',
                resource_type='ConversationSession',
                resource_id='',
                description=f"User viewed conversations (count: {conversations.count()})"
            )
            
            return Response({
                'count': conversations.count(),
                'conversations': serializer.data
            }, status=status.HTTP_200_OK)
        
        except Exception as e:
            print(f"Error listing conversations: {str(e)}")
            return Response(
                {'error': 'Failed to load conversations'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def retrieve(self, request, pk=None):
        """GET /api/v1/conversations/{id}/ - Get conversation details."""
        try:
            conversation = self.get_queryset().get(id=pk)
            
            serializer = ConversationSerializer(
                conversation, context={'request': request}
            )
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='CONVERSATION_STARTED',
                resource_type='ConversationSession',
                resource_id=str(pk),
                description=f"User viewed conversation {pk}"
            )
            
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        except ConversationSession.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error retrieving conversation: {str(e)}")
            return Response(
                {'error': 'Failed to load conversation'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        """GET /api/v1/conversations/{id}/messages/ - Get conversation messages."""
        try:
            conversation = self.get_queryset().get(id=pk)
            
            # Get messages ordered by creation time
            messages = conversation.messages.all().order_by('created_at')
            
            serializer = MessageSerializer(messages, many=True)
            
            return Response({
                'conversation_id': str(pk),
                'message_count': messages.count(),
                'messages': serializer.data
            }, status=status.HTTP_200_OK)
        
        except ConversationSession.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error getting messages: {str(e)}")
            return Response(
                {'error': 'Failed to load messages'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def send_message(self, request, pk=None):
        """POST /api/v1/conversations/{id}/messages/ - Send message in conversation"""
        
        try:
            conversation = self.get_queryset().get(id=pk)
            
            message_body = request.data.get('message', '').strip()
            if not message_body:
                return Response(
                    {'error': 'Message cannot be empty'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create message record
            message = Message.objects.create(
                conversation=conversation,
                sender=request.user,
                message_type='PATIENT' if request.user.role == 'PATIENT' else 'CLINICIAN',
                content=message_body,
                delivery_status='PENDING'
            )
            
            # ========== NEW: HANDLE PATIENT MESSAGES ==========
            if request.user.role == 'PATIENT':
                # Patient sent message
                
                if conversation.status in ['PENDING_CLINICIAN_REVIEW', 'DIRECT_MESSAGING']:
                    # Just save, don't send anywhere (yet)
                    message.delivery_status = 'DELIVERED'
                    message.save()
                    
                    # ========== NOTIFY CLINICIAN ON WHATSAPP ==========
                    if conversation.assigned_clinician:
                        try:
                            from apps.clinician.whatsapp_handler import ClinicianWhatsAppHandler
                            handler = ClinicianWhatsAppHandler()
                            handler.notify_patient_message(
                                conversation.assigned_clinician,
                                conversation,
                                message_body
                            )
                            logger.info(f"✅ Notified clinician about patient message")
                        except Exception as e:
                            print(f"Error notifying clinician: {str(e)}")
                    # ================================================
                    
                    return Response({
                        'status': 'Message saved',
                        'message_id': str(message.id)
                    }, status=status.HTTP_201_CREATED)
            
            # ========== CLINICIAN SENDING MESSAGE ==========
            elif request.user.role == 'CLINICIAN':
                # Clinician sent message - send to patient via WhatsApp
                if conversation.assigned_clinician == request.user:
                    try:
                        twilio = TwilioClient()
                        twilio.send_message(conversation.patient.whatsapp_id, message_body)
                        message.delivery_status = 'SENT'
                        message.save()
                    except Exception as e:
                        print(f"Error sending to patient: {str(e)}")
                        message.delivery_status = 'FAILED'
                        message.save()
            # ================================================
            
            # Log
            AuditLog.objects.create(
                user=request.user,
                action_type='MESSAGE_SENT',
                resource_type='Message',
                resource_id=str(message.id),
                description=f"Message sent in conversation"
            )
            
            serializer = MessageSerializer(message)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        except ConversationSession.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error sending message: {str(e)}")
            return Response(
                {'error': 'Failed to send message'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def triage_questions(self, request, pk=None):
        """GET /api/v1/conversations/{id}/triage-questions/ - Get triage questions."""
        try:
            conversation = self.get_queryset().get(id=pk)
            
            # Only show if user is patient or assigned clinician
            if request.user != conversation.patient and request.user != conversation.assigned_clinician:
                return Response(
                    {'error': 'Access denied'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            questions = conversation.triage_questions.all().order_by('question_order')
            
            serializer = TriageQuestionSerializer(questions, many=True)
            
            return Response({
                'conversation_id': str(pk),
                'questions_count': questions.count(),
                'questions': serializer.data
            }, status=status.HTTP_200_OK)
        
        except ConversationSession.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error getting triage questions: {str(e)}")
            return Response(
                {'error': 'Failed to load triage questions'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """POST /api/v1/conversations/{id}/close/ - Close conversation."""
        try:
            conversation = self.get_queryset().get(id=pk)
            
            # Only patient or assigned clinician can close
            if request.user != conversation.patient and request.user != conversation.assigned_clinician:
                return Response(
                    {'error': 'Access denied'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            conversation.status = 'CLOSED'
            conversation.closed_at = timezone.now()
            conversation.save()
            
            # Log action
            AuditLog.objects.create(
                user=request.user,
                action_type='CONVERSATION_CLOSED',
                resource_type='ConversationSession',
                resource_id=str(pk),
                description=f"User closed conversation {pk}"
            )
            
            return Response(
                {'status': 'Conversation closed'},
                status=status.HTTP_200_OK
            )
        
        except ConversationSession.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error closing conversation: {str(e)}")
            return Response(
                {'error': 'Failed to close conversation'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def assessment(self, request, pk=None):
        """GET /api/v1/conversations/{id}/assessment/ - Get assessment for conversation."""
        try:
            conversation = self.get_queryset().get(id=pk)
            
            try:
                assessment = conversation.assessment
                
                from apps.assessments.serializers import AssessmentDetailSerializer
                serializer = AssessmentDetailSerializer(assessment)
                
                return Response(serializer.data, status=status.HTTP_200_OK)
            
            except AIAssessment.DoesNotExist:
                return Response(
                    {'error': 'No assessment found for this conversation'},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        except ConversationSession.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            print(f"Error getting assessment: {str(e)}")
            return Response(
                {'error': 'Failed to load assessment'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )