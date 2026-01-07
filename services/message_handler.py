from pydoc import text
import requests
import logging
import json
from datetime import datetime
from django.utils import timezone 
from django.conf import settings
from apps.authentication.models import User, PatientProfile, ClinicianProfile
from apps.conversations.models import ConversationSession, Message, TriageQuestion
from apps.assessments.models import AIAssessment
from apps.escalations.models import EscalationAlert, EscalationRule
from apps.audit.models import AuditLog
from integrations.twilio.client import TwilioClient
from services.groq_service import GroqService
from services.ai_engine import AIEngine
from requests.auth import HTTPBasicAuth


logger = logging.getLogger('lifegate')


class MessageHandler:
    """Main handler for incoming WhatsApp messages."""
    
    WELCOME_MESSAGE = """ *LIFEGATE MOBILE*
_Telemedicine Platform_

Welcome! 👋

We connect you with qualified, licensed clinicians who can assess your symptoms and provide professional medical guidance.

*IMPORTANT - READ FIRST*

This service is NOT for emergencies. If you're experiencing a life-threatening emergency or severe symptoms, please call emergency services immediately.


*USER AGREEMENT*

By clicking "GET STARTED," you agree to:

✅ This is for health assessment only, NOT a diagnosis
✅ A licensed clinician will review my case
✅ My information is encrypted and confidential
✅ I understand the limitations of this service

*Key Points:*
• Response time: Usually within 10 minutes, up to 1 hour
• All conversations are private & secure
• Your health data is protected
• Clinicians are licensed professionals

To continue, reply:
👉 *GET STARTED* - I agree and want to proceed
👉 *DECLINE* - I don't want to continue"""
    
    PROFILE_QUESTIONS = {
        'age': "Great! To provide the best care, may I ask a few quick questions? What's your age?",
        'gender': "Thanks! What's your gender? Reply: Male, Female, or Other",
        'chief_complaint': "Perfect! Now, what brings you here today? Please describe what's bothering you.",
    }
    
    def __init__(self):
        self.twilio = TwilioClient()
        self.groq = GroqService()
        self.ai_engine = AIEngine()
    
    def process_incoming_message(self, incoming_data):
        """
        Main webhook handler for incoming WhatsApp messages.
        
        Args:
            incoming_data: dict with from, body, etc from Twilio
        """
        #debug
        print("Incoming webhook received!")
        print(incoming_data)
        try:
            whatsapp_id = incoming_data.get('From')
            # Check if message contains media (voice note)
            message_body = incoming_data.get('Body', '').strip()
            media_url = incoming_data.get('MediaUrl0')
            media_type = incoming_data.get('MediaContentType0')

            if media_url:
                print("🎤 Voice message detected")
                transcription = self._transcribe_audio(media_url)

                if transcription:
                    message_body = self._normalize_transcription(transcription)
                else:
                    message_body = ""

            else:
                message_body = incoming_data.get('Body', '').strip() or "[Empty message]"

            logger.info(f"Processing message from {whatsapp_id}: {message_body[:50]}")
            
            # Step 1: Get or create user
            user, created = self._get_or_create_user(whatsapp_id)
            if not user:
                print(f"Failed to create user for {whatsapp_id}")
                return False
            
            if created:
                logger.info(f"Auto-registered new patient: {user.phone_number}")
            
            # Step 2: Get or create conversation session
            conversation = self._get_or_create_conversation(user)
            
            # Step 3: Save incoming message
            # ✅ Ensure Message model has media_url and media_type fields
            message = Message.objects.create(
                conversation=conversation,
                sender=user,
                message_type='PATIENT',
                content=message_body,
                media_url=media_url,
                media_type=media_type,
                delivery_status='DELIVERED'
            )
            
            # Step 4: Log action
            AuditLog.objects.create(
                user=user,
                action_type='MESSAGE_RECEIVED',
                resource_type='Message',
                resource_id=str(message.id),
                description=f"Patient sent message: {message_body[:100]}"
            )
            
            # Step 5: Route based on conversation status
            if conversation.status == 'INITIAL':
                self._send_welcome_screen(user, conversation)
            
            elif conversation.status == 'AWAITING_ACCEPTANCE':
                self._handle_acceptance(user, conversation, message_body)
            
            elif conversation.status == 'AWAITING_PATIENT_PROFILE':
                self._handle_profile_collection(user, conversation, message_body)
            
            elif conversation.status == 'AI_TRIAGE_IN_PROGRESS':
                self._handle_triage_response(user, conversation, message_body)
            
            elif conversation.status == 'PENDING_CLINICIAN_REVIEW':
                self._handle_pending_review(user, conversation, message_body)
            
            elif conversation.status == 'DIRECT_MESSAGING':
                self._handle_direct_message(user, conversation, message_body)
            
            return True
            
        except Exception as e:
            logger.exception(f"Error processing message from {whatsapp_id}")
            return False
        
    # ✅ Updated _transcribe_audio method
    from requests.auth import HTTPBasicAuth

    def _transcribe_audio(self, media_url):
        print("🎧 Downloading voice note from Twilio...")

        try:
            response = requests.get(
                media_url,
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                timeout=15
            )
            response.raise_for_status()

            audio_bytes = response.content
            print(f"✅ Audio downloaded ({len(audio_bytes)} bytes)")

            print("🎧 Sending audio to Groq Whisper...")
            transcription = self.groq.transcribe_audio(media_url)


            print(f"📝 Transcription result: {transcription}")
            return transcription.strip()

        except Exception as e:
            print("❌ Voice transcription failed")
            import traceback
            traceback.print_exc()
            return ""



    # Rest of your code remains the same
    def _get_or_create_user(self, whatsapp_id):
        """Auto-register patient if first time."""
        try:
            phone = whatsapp_id.replace('whatsapp:', '')
            user = User.objects.filter(phone_number=phone).first()
            if not user:
                username = f"patient_{phone.replace('+', '')}"
                user = User.objects.create_user(
                    username=username,
                    phone_number=phone,
                    whatsapp_id=whatsapp_id,
                    role='PATIENT'
                )
                PatientProfile.objects.create(user=user)
                return user, True
            return user, False
        except Exception as e:
            print(f"Error in _get_or_create_user: {str(e)}")
            return None, False
    
    # ... rest of your methods remain unchanged

    
    def _get_or_create_conversation(self, user):
        """Get active conversation or create new one."""
        conversation = ConversationSession.objects.filter(
            patient=user,
            status__in=['INITIAL', 'AWAITING_ACCEPTANCE', 'AWAITING_PATIENT_PROFILE',
                       'AI_TRIAGE_IN_PROGRESS', 'PENDING_CLINICIAN_REVIEW', 'DIRECT_MESSAGING']
        ).first()
        
        if not conversation:
            conversation = ConversationSession.objects.create(
                patient=user,
                status='INITIAL'
            )
        
        return conversation
    
    def _send_welcome_screen(self, user, conversation):
        """Send welcome message with user agreement."""
        try:
            self.twilio.send_message(user.whatsapp_id, self.WELCOME_MESSAGE)
            
            conversation.status = 'AWAITING_ACCEPTANCE'
            conversation.save()
            
            Message.objects.create(
                conversation=conversation,
                sender=None,
                message_type='SYSTEM',
                content=self.WELCOME_MESSAGE,
                delivery_status='SENT'
            )
            
            logger.info(f"Welcome screen sent to {user.phone_number}")
        except Exception as e:
            print(f"Error sending welcome screen: {str(e)}")
    
    def _handle_acceptance(self, user, conversation, message_body):
        """Handle user agreement acceptance."""
        if message_body.upper() == 'GET STARTED':
            user.terms_accepted = True
            user.terms_accepted_at = timezone.now()
            user.save()
            
            conversation.status = 'AWAITING_PATIENT_PROFILE'
            conversation.save()
            
            # Ask for age
            self.twilio.send_message(user.whatsapp_id, self.PROFILE_QUESTIONS['age'])
            
            Message.objects.create(
                conversation=conversation,
                sender=None,
                message_type='SYSTEM',
                content=self.PROFILE_QUESTIONS['age'],
                delivery_status='SENT'
            )
            
            logger.info(f"User {user.phone_number} accepted terms")
        
        elif message_body.upper() == 'DECLINE':
            conversation.status = 'CLOSED'
            conversation.closed_at = timezone.now()
            conversation.save()
            
            self.twilio.send_message(
                user.whatsapp_id,
                "Thank you for your interest. If you change your mind, feel free to reach out anytime."
            )
            logger.info(f"User {user.phone_number} declined terms")
    
    def _handle_profile_collection(self, user, conversation, message_body):
        """Collect patient age and gender."""
        try:
            profile = user.patient_profile
            
            # Check if we have age
            if not profile.age:
                try:
                    age = int(message_body)
                    if 0 < age < 150:
                        profile.age = age
                        profile.save()
                        
                        # Ask for gender
                        self.twilio.send_message(user.whatsapp_id, self.PROFILE_QUESTIONS['gender'])
                        Message.objects.create(
                            conversation=conversation,
                            sender=None,
                            message_type='SYSTEM',
                            content=self.PROFILE_QUESTIONS['gender'],
                            delivery_status='SENT'
                        )
                        return
                except ValueError:
                    self.twilio.send_message(user.whatsapp_id, "Please enter a valid age (number)")
                    return
            
            # Check if we have gender
            if not profile.gender:
                gender_map = {'MALE': 'MALE', 'FEMALE': 'FEMALE', 'OTHER': 'OTHER'}
                gender_input = message_body.upper()
                
                if gender_input in gender_map:
                    profile.gender = gender_map[gender_input]
                    profile.save()
                    
                    # Ask for chief complaint
                    self.twilio.send_message(user.whatsapp_id, self.PROFILE_QUESTIONS['chief_complaint'])
                    Message.objects.create(
                        conversation=conversation,
                        sender=None,
                        message_type='SYSTEM',
                        content=self.PROFILE_QUESTIONS['chief_complaint'],
                        delivery_status='SENT'
                    )
                    return
                else:
                    self.twilio.send_message(user.whatsapp_id, "Please reply: Male, Female, or Other")
                    return
            
            # If we have age and gender, process chief complaint
            if message_body and len(message_body) > 5:
                conversation.chief_complaint = message_body
                conversation.status = 'AI_TRIAGE_IN_PROGRESS'
                conversation.save()
                
                # Check for red flags
                if self._check_red_flags(message_body):
                    self._handle_escalation(user, conversation, message_body)
                    return
                
                # Start AI triage
                self._start_ai_triage(user, conversation)
        
        except Exception as e:
            print(f"Error in profile collection: {str(e)}")
            self.twilio.send_message(user.whatsapp_id, "An error occurred. Please try again.")
    
    def _check_red_flags(self, text):
        """Check if message contains red flag keywords."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in settings.RED_FLAG_KEYWORDS)
    
    def _handle_escalation(self, user, conversation, trigger_text):
        """Handle escalation and notify clinician"""
        
        from apps.escalations.models import EscalationAlert
        from apps.clinician.whatsapp_handler import ClinicianWhatsAppHandler
        
        conversation.is_escalated = True
        conversation.status = 'ESCALATED'
        conversation.save()
        
        escalation = EscalationAlert.objects.create(
            conversation=conversation,
            alert_status='PENDING',
            alert_message=f"Red flag: {trigger_text}",
            alert_severity='CRITICAL'
        )
        
        # Notify assigned clinician
        if conversation.assigned_clinician:
            handler = ClinicianWhatsAppHandler()
            handler.notify_escalation(conversation.assigned_clinician, escalation)
    
    def _start_ai_triage(self, user, conversation):
        """Start AI-based triage questions."""
        try:
            profile = user.patient_profile
            
            question = self.ai_engine.generate_first_question(
                age=profile.age,
                gender=profile.gender,
                chief_complaint=conversation.chief_complaint
            )
            
            # Save question
            triage_q = TriageQuestion.objects.create(
                conversation=conversation,
                question_text=question,
                question_type='OPEN_ENDED',
                question_order=1
            )
            
            # Send to patient
            self.twilio.send_message(user.whatsapp_id, question)
            Message.objects.create(
                conversation=conversation,
                sender=None,
                message_type='AI_QUERY',
                content=question,
                delivery_status='SENT'
            )
            
            conversation.ai_questions_asked = 1
            conversation.save()
            
            logger.info(f"Triage started for {user.phone_number}")
        except Exception as e:
            print(f"Error starting triage: {str(e)}")
            self.twilio.send_message(user.whatsapp_id, "An error occurred. Please try again later.")
    
    def _handle_triage_response(self, user, conversation, message_body):
        """Process triage question response."""
        try:
            # ✅ GUARD CLAUSE: Check for empty message
            if not message_body or message_body.strip() == "":
                self.twilio.send_message(
                    user.whatsapp_id,
                    "I didn't catch that. Could you please repeat your answer?"
                )
                return
            
            # Get last unanswered question
            last_question = conversation.triage_questions.filter(
                response_processed=False
            ).order_by('question_order').first()
            
            if last_question:
                last_question.patient_response = message_body
                last_question.response_timestamp = timezone.now()
                last_question.response_processed = True
                last_question.save()
            
            conversation.ai_questions_asked += 1
            
            # Check if we've asked enough questions
            if conversation.ai_questions_asked >= settings.MAX_TRIAGE_QUESTIONS:
                self._generate_assessment(user, conversation)
            else:
                # Generate next question
                try:
                    next_question = self.ai_engine.generate_next_question(
                        conversation=conversation,
                        current_response=message_body
                    )
                    
                    # ✅ CRITICAL VALIDATION: Check if AI returned a valid question
                    if not next_question or not next_question.strip():
                        print("❌ AI returned empty question - using fallback")
                        next_question = "Can you tell me more about your symptoms? Any other details that might help?"
                    
                    # Now safe to save to database
                    triage_q = TriageQuestion.objects.create(
                        conversation=conversation,
                        question_text=next_question,
                        question_type='OPEN_ENDED',
                        question_order=conversation.ai_questions_asked + 1
                    )
                    
                    self.twilio.send_message(user.whatsapp_id, next_question)
                    Message.objects.create(
                        conversation=conversation,
                        sender=None,
                        message_type='AI_QUERY',
                        content=next_question,
                        delivery_status='SENT'
                    )
                    
                except Exception as ai_error:
                    print(f"❌ AI question generation failed: {str(ai_error)}")
                    # Use fallback question
                    fallback_question = "Thank you. Can you describe any other symptoms you're experiencing?"
                    
                    triage_q = TriageQuestion.objects.create(
                        conversation=conversation,
                        question_text=fallback_question,
                        question_type='OPEN_ENDED',
                        question_order=conversation.ai_questions_asked + 1
                    )
                    
                    self.twilio.send_message(user.whatsapp_id, fallback_question)
                    Message.objects.create(
                        conversation=conversation,
                        sender=None,
                        message_type='AI_QUERY',
                        content=fallback_question,
                        delivery_status='SENT'
                    )
            
            conversation.save()
            
        except Exception as e:
            print(f"Error handling triage response: {str(e)}")
            import traceback
            traceback.print_exc()
            self.twilio.send_message(
                user.whatsapp_id,
                "An error occurred. Please try again."
            )    


    def _generate_assessment(self, user, conversation):
        """Generate final AI assessment."""
        try:
            assessment_json = self.ai_engine.generate_assessment(conversation)
            
            assessment = AIAssessment.objects.create(
                conversation=conversation,
                patient=user,
                patient_age=user.patient_profile.age,
                patient_gender=user.patient_profile.gender,
                chief_complaint=conversation.chief_complaint,
                symptoms_overview=assessment_json.get('symptoms_overview', {}),
                key_observations=assessment_json.get('key_observations', {}),
                preliminary_recommendations=assessment_json.get('preliminary_recommendations', {}),
                otc_suggestions=assessment_json.get('otc_suggestions', {}),
                monitoring_advice=assessment_json.get('monitoring_advice', {}),
                red_flags_detected=assessment_json.get('red_flags_detected', []),
                confidence_score=assessment_json.get('confidence_score', 0.0),
                status='GENERATED'
            )
            
            conversation.status = 'PENDING_CLINICIAN_REVIEW'
            conversation.triage_completed_at = timezone.now()
            conversation.save()
            
            # Assign clinician
            self._assign_clinician(conversation)
            
            # Notify patient
            self.twilio.send_message(
                user.whatsapp_id,
                "Thank you! A clinician is reviewing your details. "
                "You'll receive recommendations soon."
            )
            
            logger.info(f"Assessment generated for {user.phone_number}")
        except Exception as e:
            print(f"Error generating assessment: {str(e)}")
            self.twilio.send_message(user.whatsapp_id, "An error occurred. Please try again later.")
    
    def _assign_clinician(self, conversation):
        """Assign clinician and notify them"""
        
        from apps.clinician.models import ClinicianAvailability, PatientAssignment
        from apps.clinician.whatsapp_handler import ClinicianWhatsAppHandler
        
        available = ClinicianAvailability.objects.filter(
            status__in=['AVAILABLE', 'ON_CALL']
        ).order_by('current_patient_count')[:1]
        
        if available:
            clinician = available[0].clinician
            
            conversation.assigned_clinician = clinician
            conversation.clinician_assigned_at = timezone.now()
            conversation.status = 'PENDING_CLINICIAN_REVIEW'
            conversation.save()
            
            # Create assignment
            PatientAssignment.objects.create(
                patient=conversation.patient,
                clinician=clinician,
                conversation=conversation,
                assignment_reason='AUTO_MATCH'
            )
            
            # Send WhatsApp notification to clinician
            try:
                handler = ClinicianWhatsAppHandler()
                handler.notify_new_patient(clinician, conversation)
            except Exception as e:
                print(f"Error notifying clinician: {str(e)}")
    
    def _handle_pending_review(self, user, conversation, message_body):
        """Handle messages while assessment is pending clinician review."""
        # Store message for clinician to see
        Message.objects.create(
            conversation=conversation,
            sender=user,
            message_type='PATIENT',
            content=message_body,
            delivery_status='DELIVERED'
        )
        
        self.twilio.send_message(
            user.whatsapp_id,
            "Your message has been received. The clinician will respond shortly."
        )
    
    def _handle_direct_message(self, user, conversation, message_body):
        """Handle direct patient-clinician messaging."""
        Message.objects.create(
            conversation=conversation,
            sender=user,
            message_type='PATIENT',
            content=message_body,
            delivery_status='DELIVERED'
        )
        
        if conversation.assigned_clinician:
            # Notify clinician in dashboard or email
            logger.info(f"New message from patient {user.phone_number} for clinician {conversation.assigned_clinician.phone_number}")
            
            if not message_body:
                self.twilio.send_message(
                    user.whatsapp_id,
                    "Sorry, I couldn't understand the voice message. Please try again or send text."
                )
                return True
    
    def _normalize_transcription(self, text):
        text = text.lower().strip()
    
        # Allowed gender answers
        valid_genders = ["male", "female", "other"]

        # Map common mis-hearings to intended genders
        mapping = {
            "emil": "male",
            "miel": "male",
            "meal": "male",
        }
    
        # First, fix known mis-hearings
        if text in mapping:
            text = mapping[text]
    
        # Then, if the text is a valid gender, keep it
        if text in valid_genders:
            return text

        # Otherwise, return raw text (user might literally say "meal")
        return text