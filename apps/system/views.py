import logging
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from config import settings
from integrations.twilio.client import TwilioClient



logger = logging.getLogger('lifegate')


@method_decorator(csrf_exempt, name='dispatch')
class TwilioWebhookView(APIView):
    """
    POST /api/v1/twilio-webhook/
    
    Handles incoming WhatsApp messages from BOTH patients and clinicians.
    Automatically routes based on user role.
    """
    
    permission_classes = [AllowAny]
    
    def post(self, request):
        """
        Handle incoming WhatsApp message from Twilio.
        Routes to clinician or patient handler based on sender role.
        """
        
        try:
            logger.info("[WEBHOOK] Received message from Twilio")
            
            # Step 1: Validate Twilio signature
            twilio = TwilioClient()
            request_url = request.build_absolute_uri()
            signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
            
            # if not twilio.validate_request(request_url, request.POST, signature):
            #     logger.warning("[WEBHOOK] Invalid Twilio signature")
            #     return Response(
            #         {'status': 'error', 'message': 'Invalid signature'},
            #         status=status.HTTP_401_UNAUTHORIZED
            #     )
            
            if settings.DEBUG:
                is_valid = True
            else:
                is_valid = twilio.validate_request(request_url, request.POST, signature)

            if not is_valid:
                return Response({'error': 'Invalid signature'}, status=401)
            
            logger.info("[WEBHOOK] Signature validated")
            
            # Step 2: Extract message data
            incoming_data = {
                'From': request.POST.get('From'),
                'Body': request.POST.get('Body', ''),
                'MediaUrl0': request.POST.get('MediaUrl0'),
                'MessageSid': request.POST.get('MessageSid'),
            }
            
            whatsapp_id = incoming_data.get('From')
            message_body = incoming_data.get('Body', '')
            
            logger.info(f"[WEBHOOK] From: {whatsapp_id} | Body: {message_body[:50]}")
            
            # Step 3: Check if sender is clinician or patient
            from apps.authentication.models import User
            
            user = None
            try:
                user = User.objects.get(whatsapp_id=whatsapp_id)
                logger.info(f"[WEBHOOK] Found user: {user.phone_number} | Role: {user.role}")
            except User.DoesNotExist:
                logger.info(f"[WEBHOOK] New user (patient): {whatsapp_id}")
            
            # Step 4: Route to appropriate handler
            success = False
            
            if user and user.role == 'CLINICIAN':
                # CLINICIAN MESSAGE
                logger.info(f"[WEBHOOK] Routing to CLINICIAN handler")
                
                from apps.clinician.whatsapp_handler import ClinicianWhatsAppHandler
                handler = ClinicianWhatsAppHandler()
                success = handler.process_clinician_message(incoming_data)
                
            else:
                # PATIENT MESSAGE (new or existing patient)
                logger.info(f"[WEBHOOK] Routing to PATIENT handler")
                
                from services.message_handler import MessageHandler
                handler = MessageHandler()
                success = handler.process_incoming_message(incoming_data)
            
            # Step 5: Return response
            if success:
                logger.info(f"[WEBHOOK] Message processed successfully")
                return Response(
                    {'status': 'success'},
                    status=status.HTTP_200_OK
                )
            else:
                print(f"[WEBHOOK] Failed to process message")
                return Response(
                    {'status': 'error', 'message': 'Failed to process'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        except Exception as e:
            logger.exception(f"[WEBHOOK] Exception: {str(e)}", exc_info=True)
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class HealthCheckView(APIView):
    """GET /api/v1/health/ - System health check"""
    
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Check system health"""
        
        try:
            from django.db import connection
            
            # Test database
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            
            from datetime import datetime
            
            health = {
                'status': 'healthy',
                'database': 'connected',
                'timestamp': datetime.now().isoformat(),
            }
            
            return Response(health, status=status.HTTP_200_OK)
        
        except Exception as e:
            print(f"[HEALTH] Health check failed: {str(e)}")
            return Response(
                {'status': 'unhealthy', 'error': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )