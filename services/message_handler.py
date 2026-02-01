# services/message_handler.py
from pydoc import text
import requests
import logging
import json
import string
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
from services.flutterwave_service import FlutterwaveService
import uuid
from apps.subscriptions.models import PatientSubscription, CreditPackage, PaymentHistory
from services.workflow_service import finalize_consultation_flow


logger = logging.getLogger('lifegate')


class MessageHandler:
    """Main handler for incoming WhatsApp messages with conversation mode system."""
    
    WELCOME_MESSAGE = """*LIFEGATE MOBILE*
_Telemedicine Platform_

Welcome! 👋

*IMPORTANT - READ FIRST*

This service is NOT for emergencies. If you're experiencing a life-threatening emergency or severe symptoms, please call emergency services immediately.

*USER AGREEMENT*

By clicking "GET STARTED," you agree to:

✅ Information provided is for health guidance only
✅ My information is encrypted and confidential
✅ I understand the limitations of this service
✅ I will seek emergency care if needed

*Response Time:*
• Usually within 10-60 minutes
• All conversations are private & secure

To continue, reply:
👉 *GET STARTED* - I agree and want to proceed
👉 *DECLINE* - I don't want to continue"""

    MODE_SELECTION_MESSAGE = """Great! Now, how would you like to proceed?

*1️⃣ TALK TO AI (FREE)*
Get general health information and guidance from our AI assistant. Good for:
• Understanding symptoms
• General health questions
• Wellness advice
• Basic information

⚠️ *Note:* AI cannot diagnose or prescribe

*2️⃣ SEE A CLINICIAN (PAID)*
Connect with a licensed medical professional who can:
• Assess your symptoms
• Provide diagnosis
• Issue prescriptions
• Give professional medical advice

💰 *Cost:* Uses 1 consultation credit

Reply with:
👉 *1* for AI Chat (Free)
👉 *2* for Clinician (Paid)"""

    AI_ONLY_DISCLAIMER = """Perfect! You're now chatting with our AI assistant. 🤖

*What I CAN do:*
✅ Answer general health questions
✅ Explain medical terms
✅ Provide wellness information
✅ Discuss symptoms generally

*What I CANNOT do:*
❌ Diagnose medical conditions
❌ Prescribe medications
❌ Replace a doctor's visit
❌ Handle emergencies

If at any point you need a licensed clinician, just let me know!

Now, what would you like to know?"""

    CLINICIAN_MODE_START = """Excellent! You'll be connected with a licensed clinician. 🩺

Let me collect some information first so the doctor has context when they review your case.

What's your age?"""
    
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
        """Main webhook handler for incoming WhatsApp messages."""
        print("Incoming webhook received!")
        print(incoming_data)
        
        self.incoming_data = incoming_data
        
        try:
            whatsapp_id = incoming_data.get('From')
            media_url = incoming_data.get('MediaUrl0')
            media_type = incoming_data.get('MediaContentType0')

            # Handle voice messages
            if media_url:
                print("🎤 Voice message detected")
                transcription = self._transcribe_audio(media_url)
                # WHY CHANGED: Previously called _normalize_transcription() here which
                # lowercased and stripped ALL punctuation before the text reached any
                # handler. That destroyed sentence structure before red flag checks and
                # the AI engine saw it. Both of those consumers handle casing internally
                # already. Voice input now flows through the same path as typed input —
                # raw, with only .strip() for whitespace.
                message_body = transcription.strip() if transcription else ""
            else:
                message_body = incoming_data.get('Body', '').strip() or "[Empty message]"

            logger.info(f"Processing message from {whatsapp_id}: {message_body[:50]}")
            
            # Get or create user
            user, created = self._get_or_create_user(whatsapp_id)
            if not user:
                print(f"Failed to create user for {whatsapp_id}")
                return False
            
            if created:
                logger.info(f"Auto-registered new patient: {user.phone_number}")
                
            conversation = self._get_or_create_conversation(user)
            
            # WHY GATED: Previously _handle_package_selection ran for every single
            # message, before the conversation router. It checked if the message was
            # a digit and matched it against CreditPackage rows. A user in AI_ONLY_ACTIVE
            # (or any other status) who typed "1" or "2" for any reason — answering a
            # question, picking a mode, anything — got swallowed as a package selection
            # attempt before the router could handle it. Now it only activates when the
            # conversation is actually in PENDING_PAYMENT, which is the only status where
            # package selection is valid input.
            if conversation.status == 'PENDING_PAYMENT':
                if self._handle_package_selection(user, message_body):
                    return True
            
            # Save incoming message
            message = Message.objects.create(
                conversation=conversation,
                sender=user,
                message_type='PATIENT',
                content=message_body,
                media_url=media_url,
                media_type=media_type,
                delivery_status='DELIVERED'
            )
            
            # Log action
            AuditLog.objects.create(
                user=user,
                action_type='MESSAGE_RECEIVED',
                resource_type='Message',
                resource_id=str(message.id),
                description=f"Patient sent message: {message_body[:100]}"
            )
            
            # Route based on conversation status
            self._route_message(user, conversation, message_body)
            
            return True
            
        except Exception as e:
            logger.exception(f"Error processing message from {whatsapp_id}")
            return False
    
    def _route_message(self, user, conversation, message_body):
        """Route message to appropriate handler based on status."""
        
        status = conversation.status
        
        if status == 'INITIAL':
            self._send_welcome_screen(user, conversation)
        
        elif status == 'AWAITING_ACCEPTANCE':
            self._handle_acceptance(user, conversation, message_body)
        
        elif status == 'MODE_SELECTION':
            self._handle_mode_selection(user, conversation, message_body)
        
        elif status == 'AI_ONLY_ACTIVE':
            self._handle_ai_only_chat(user, conversation, message_body)
        
        elif status == 'ESCALATION_CONSENT':
            self._handle_escalation_consent(user, conversation, message_body)
        
        elif status == 'AWAITING_PATIENT_PROFILE':
            self._handle_profile_collection(user, conversation, message_body)
        
        elif status == 'AI_TRIAGE_IN_PROGRESS':
            self._handle_triage_response(user, conversation, message_body)
        
        elif status == 'PENDING_CLINICIAN_REVIEW':
            self._handle_pending_review(user, conversation, message_body)
        
        elif status == 'DIRECT_MESSAGING':
            self._handle_direct_message(user, conversation, message_body)
    
    # ==================== PAYMENT HANDLING ====================
    
    def _handle_package_selection(self, user, message_body):
        """Check if user is selecting a payment package."""
        try:
            msg_clean = message_body.strip().lower()
            
            if msg_clean.isdigit():
                idx = int(msg_clean) - 1
                packages = list(CreditPackage.objects.all().order_by('price'))
                
                if 0 <= idx < len(packages):
                    selected_pkg = packages[idx]
                    self._send_payment_link(user, selected_pkg)
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Package selection error: {e}")
            return False
    
    def _send_payment_link(self, user, pkg):
        """Generate and send payment link."""
        tx_ref = f"PKG-{user.id}-{uuid.uuid4().hex[:8]}"
        
        PaymentHistory.objects.create(
            user=user,
            package=pkg,
            reference=tx_ref,
            amount=pkg.price,
            status='PENDING'
        )
        
        flutterwave = FlutterwaveService()
        link = flutterwave.initialize_payment(user, pkg.price, tx_ref)
        
        if link:
            self.twilio.send_message(
                user.whatsapp_id,
                f"💳 *BUY {pkg.name.upper()}*\n\n"
                f"👇 Click to Pay ₦{pkg.price:,.0f}:\n{link}"
            )
        else:
            self.twilio.send_message(user.whatsapp_id, "Error generating link.")
    
    def _send_credit_menu(self, user, packages):
        """Show available credit packages."""
        msg = "🔒 *CONSULTATION CREDITS REQUIRED*\n\n"
        msg += "You have 0 credits. Please purchase a bundle to start a consultation:\n\n"
        
        for idx, pkg in enumerate(packages, 1):
            price = f"₦{pkg.price:,.0f}"
            msg += f"*{idx}. {pkg.name}*\n"
            msg += f"   {pkg.credits} Sessions @ {price}\n"
            if pkg.description:
                msg += f"   _({pkg.description})_\n"
            msg += "\n"
            
        msg += "👇 *Reply with the number* (e.g., 2) to purchase."
        self.twilio.send_message(user.whatsapp_id, msg)
    
    # ==================== VOICE TRANSCRIPTION ====================
    
    def _transcribe_audio(self, media_url):
        """Transcribe voice note using Groq."""
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
    
    def _normalize_transcription(self, transcription: str) -> str:
        """Normalize transcription text."""
        if not transcription:
            return ""
        transcription = transcription.lower()
        transcription = transcription.translate(str.maketrans('', '', string.punctuation))
        return transcription.strip()
    
    # ==================== USER MANAGEMENT ====================
    
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
    
    def _get_or_create_conversation(self, user):
        """Get active conversation or create new one."""
        conversation = ConversationSession.objects.filter(
            patient=user,
            status__in=['INITIAL', 'AWAITING_ACCEPTANCE', 'MODE_SELECTION',
                       'AI_ONLY_ACTIVE', 'ESCALATION_CONSENT',
                       'AWAITING_PATIENT_PROFILE', 'AI_TRIAGE_IN_PROGRESS',
                       'PENDING_PAYMENT', 'PENDING_CLINICIAN_REVIEW', 'DIRECT_MESSAGING']
        ).first()
        
        if not conversation:
            conversation = ConversationSession.objects.create(
                patient=user,
                status='INITIAL',
                mode='UNDECIDED'
            )
        
        return conversation
    
    # ==================== WELCOME & ACCEPTANCE ====================
    
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
            
            conversation.status = 'MODE_SELECTION'
            conversation.save()
            
            # Send mode selection
            self.twilio.send_message(user.whatsapp_id, self.MODE_SELECTION_MESSAGE)
            
            Message.objects.create(
                conversation=conversation,
                sender=None,
                message_type='SYSTEM',
                content=self.MODE_SELECTION_MESSAGE,
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


# ==================== MODE SELECTION ====================
    
    def _handle_mode_selection(self, user, conversation, message_body):
        """Handle user choosing between AI-only or Clinician mode."""
        msg_clean = message_body.strip()
        
        if msg_clean == '1':
            # AI-ONLY MODE
            conversation.mode = 'AI_ONLY'
            conversation.status = 'AI_ONLY_ACTIVE'
            conversation.save()
            
            self.twilio.send_message(user.whatsapp_id, self.AI_ONLY_DISCLAIMER)
            
            Message.objects.create(
                conversation=conversation,
                sender=None,
                message_type='SYSTEM',
                content=self.AI_ONLY_DISCLAIMER,
                delivery_status='SENT'
            )
            
            logger.info(f"User {user.phone_number} chose AI_ONLY mode")
        
        elif msg_clean == '2':
            # CLINICIAN MODE
            conversation.mode = 'CLINICIAN'
            conversation.status = 'AWAITING_PATIENT_PROFILE'
            conversation.save()
            
            self.twilio.send_message(user.whatsapp_id, self.CLINICIAN_MODE_START)
            
            Message.objects.create(
                conversation=conversation,
                sender=None,
                message_type='SYSTEM',
                content=self.CLINICIAN_MODE_START,
                delivery_status='SENT'
            )
            
            logger.info(f"User {user.phone_number} chose CLINICIAN mode")
        
        else:
            # Invalid input
            self.twilio.send_message(
                user.whatsapp_id,
                "Please reply with *1* for AI Chat or *2* for Clinician."
            )
    
    # ==================== AI-ONLY MODE HANDLERS ====================
    
    def _handle_ai_only_chat(self, user, conversation, message_body):
        """Handle AI-only chat with guardrails."""
        
        # 1. Check if user explicitly wants to connect to doctor
        # REASON FOR CHANGE: Users were saying "connect me to doctor" but system didn't recognize it
        # This detects explicit escalation requests like "connect me", "talk to doctor", etc.
        if self._user_requesting_doctor(message_body):
            self._trigger_escalation_request(user, conversation, message_body)
            return
        
        # 2. Check for red flags
        if self._check_red_flags(message_body):
            self._trigger_escalation_request(user, conversation, message_body)
            return
        
        # 3. Check if message requires clinical judgment
        if self._requires_clinical_assessment(message_body):
            self._trigger_escalation_request(user, conversation, message_body)
            return
        
        # 3. Generate AI response
        try:
            ai_response = self.ai_engine.generate_ai_only_response(
                conversation=conversation,
                user_message=message_body
            )
            
            # 4. Send response
            self.twilio.send_message(user.whatsapp_id, ai_response)
            
            Message.objects.create(
                conversation=conversation,
                sender=None,
                message_type='AI_RESPONSE',
                content=ai_response,
                delivery_status='SENT'
            )
            
            logger.info(f"AI-only response sent to {user.phone_number}")
            
        except Exception as e:
            logger.error(f"AI response error: {e}")
            self.twilio.send_message(
                user.whatsapp_id,
                "I'm having trouble processing that. Could you rephrase your question?"
            )
    
    def _requires_clinical_assessment(self, text):
        """Check if message requires clinical judgment."""
        clinical_triggers = [
            'diagnose', 'diagnosis', 'what do i have', 'is it', 'do i have',
            'prescription', 'prescribe', 'medication for', 'treatment for',
            'how long', 'when should i', 'is this serious', 'should i worry',
            'broken', 'fracture', 'injury', 'accident', 'bleeding', 'pain',
            'severe', 'urgent', 'emergency'
        ]
        
        text_lower = text.lower()
        return any(trigger in text_lower for trigger in clinical_triggers)
    
    def _user_requesting_doctor(self, text):
        """
        Check if user is explicitly asking to connect to a doctor.
        
        REASON FOR ADDITION: Users say things like "connect me to doctor", "I want to see a doctor",
        "talk to clinician" but the system wasn't detecting these requests.
        This ensures explicit escalation requests are honored immediately.
        """
        doctor_request_keywords = [
            'connect me', 'talk to doctor', 'see a doctor', 'speak to doctor',
            'need a doctor', 'want a doctor', 'talk to clinician', 'see clinician',
            'connect to doctor', 'get a doctor', 'consult doctor', 'doctor please',
            'real doctor', 'human doctor', 'actual doctor'
        ]
        
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in doctor_request_keywords)
    
    def _trigger_escalation_request(self, user, conversation, trigger_text):
        """Request consent to escalate to clinician."""
        conversation.status = 'ESCALATION_CONSENT'
        conversation.escalation_reason = trigger_text[:200]
        conversation.save()
        
        escalation_msg = """🩺 *CLINICIAN CONSULTATION NEEDED*

Based on what you've shared, you need a licensed clinician to properly help you.

A doctor can:
✅ Assess your condition
✅ Provide diagnosis
✅ Prescribe treatment if needed

💰 *Cost:* 1 consultation credit

*Would you like to connect with a clinician?*

Reply:
👉 *YES* - Connect me with a doctor
👉 *NO* - Continue chatting with AI"""
        
        self.twilio.send_message(user.whatsapp_id, escalation_msg)
        
        Message.objects.create(
            conversation=conversation,
            sender=None,
            message_type='SYSTEM',
            content=escalation_msg,
            delivery_status='SENT'
        )
        
        logger.info(f"Escalation requested for {user.phone_number}: {trigger_text[:50]}")
    
    def _handle_escalation_consent(self, user, conversation, message_body):
        """Handle response to escalation request."""
        msg_upper = message_body.upper().strip()
        
        if msg_upper == 'YES':
            # Switch to clinician mode
            conversation.mode = 'CLINICIAN'
            conversation.status = 'AWAITING_PATIENT_PROFILE'
            conversation.is_escalated = True
            conversation.save()
            
            self.twilio.send_message(
                user.whatsapp_id,
                "Perfect! Connecting you with a clinician. Let me collect some information first.\n\nWhat's your age?"
            )
            
            logger.info(f"User {user.phone_number} consented to escalation")
        
        elif msg_upper == 'NO':
            # Return to AI-only mode
            conversation.status = 'AI_ONLY_ACTIVE'
            conversation.save()
            
            self.twilio.send_message(
                user.whatsapp_id,
                "No problem! I'll continue helping with general information. What else would you like to know?"
            )
            
            logger.info(f"User {user.phone_number} declined escalation")
        
        else:
            self.twilio.send_message(
                user.whatsapp_id,
                "Please reply *YES* to see a clinician or *NO* to continue with AI."
            )
    
    # ==================== CLINICIAN MODE HANDLERS ====================
    
    def _handle_profile_collection(self, user, conversation, message_body):
        """Collect patient age, gender, and chief complaint for clinician mode."""
        try:
            profile = user.patient_profile
            
            # Collect age
            if not profile.age:
                try:
                    age = int(message_body)
                    if 0 < age < 150:
                        profile.age = age
                        profile.save()
                        
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
            
            # Collect gender
            if not profile.gender:
                gender_map = {'MALE': 'MALE', 'FEMALE': 'FEMALE', 'OTHER': 'OTHER'}
                gender_input = message_body.upper()
                
                if gender_input in gender_map:
                    profile.gender = gender_map[gender_input]
                    profile.save()
                    
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
            
            # Process chief complaint
            if message_body and len(message_body) > 3:
                if message_body.lower() in ['hi', 'hello', 'hey']:
                    self.twilio.send_message(user.whatsapp_id, self.PROFILE_QUESTIONS['chief_complaint'])
                    return
                
                conversation.chief_complaint = message_body
                conversation.status = 'AI_TRIAGE_IN_PROGRESS'
                conversation.save()
                
                # Check for red flags
                if self._check_red_flags(message_body):
                    self._handle_escalation(user, conversation, message_body)
                    return
                
                # Start AI triage
                self._start_ai_triage(user, conversation)
            else:
                self.twilio.send_message(user.whatsapp_id, "Please describe your symptoms in a bit more detail.")
        
        except Exception as e:
            print(f"Error in profile collection: {str(e)}")
            self.twilio.send_message(user.whatsapp_id, "An error occurred. Please try again.")
    
    def _check_red_flags(self, text):
        """Check if message contains red flag keywords."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in settings.RED_FLAG_KEYWORDS)
    
    def _handle_escalation(self, user, conversation, trigger_text):
        """Handle critical escalation and notify clinician."""
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
        
        if conversation.assigned_clinician:
            handler = ClinicianWhatsAppHandler()
            handler.notify_escalation(conversation.assigned_clinician, escalation)
    
    def _start_ai_triage(self, user, conversation):
        """Start AI-based triage questions for clinician mode."""
        try:
            profile = user.patient_profile
            
            question = self.ai_engine.generate_first_question(
                age=profile.age,
                gender=profile.gender,
                chief_complaint=conversation.chief_complaint
            )
            
            triage_q = TriageQuestion.objects.create(
                conversation=conversation,
                question_text=question,
                question_type='OPEN_ENDED',
                question_order=1
            )
            
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
            if not message_body or message_body.strip() == "":
                self.twilio.send_message(
                    user.whatsapp_id,
                    "I didn't catch that. Could you please repeat your answer?"
                )
                return
            
            last_question = conversation.triage_questions.filter(
                response_processed=False
            ).order_by('question_order').first()
            
            if last_question:
                last_question.patient_response = message_body
                last_question.response_timestamp = timezone.now()
                last_question.response_processed = True
                last_question.save()
            
            conversation.ai_questions_asked += 1
            
            if conversation.ai_questions_asked >= settings.MAX_TRIAGE_QUESTIONS:
                self._generate_assessment(user, conversation)
            else:
                try:
                    next_question = self.ai_engine.generate_next_question(
                        conversation=conversation,
                        current_response=message_body
                    )
                    
                    if not next_question or not next_question.strip():
                        print("❌ AI returned empty question - using fallback")
                        next_question = "Can you tell me more about your symptoms? Any other details that might help?"
                    
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
            self.twilio.send_message(user.whatsapp_id, "An error occurred. Please try again.")
    
    def _generate_assessment(self, user, conversation):
        """Generate AI assessment and handle payment/credit flow."""
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
                status='PENDING_PAYMENT'
            )
            
            profile = user.patient_profile
            
            if profile.consultation_credits > 0:
                logger.info(f"User {user.phone_number} has credits. Unlocking immediately.")
                
                patient_msg = self._format_patient_summary(assessment, conversation)
                self.twilio.send_message(user.whatsapp_id, patient_msg)
                
                finalize_consultation_flow(user, conversation, assessment)
                
            else:
                logger.info(f"User {user.phone_number} has 0 credits. Pausing for payment.")
                
                conversation.status = 'PENDING_PAYMENT'
                conversation.save()
                
                msg = "✅ *ASSESSMENT COMPLETE*\n\n"
                msg += "We have analyzed your symptoms.\n"
                msg += "To unlock your full results and have a doctor review your case, please use a credit.\n\n"
                msg += "🔒 *Balance: 0 Credits*\n"
                msg += "👇 *Select a package to unlock:*"
                
                self.twilio.send_message(user.whatsapp_id, msg)
                
                packages = list(CreditPackage.objects.all().order_by('price'))
                self._send_credit_menu(user, packages)

        except Exception as e:
            logger.error(f"Error generating assessment: {str(e)}")
            self.twilio.send_message(user.whatsapp_id, "An error occurred generating your results. Please try again later.")
    
    def _format_patient_summary(self, assessment, conversation):
        """Create a casual, conversational summary for the patient."""
        try:
            symptoms_data = assessment.symptoms_overview or {}
            obs_data = assessment.key_observations or {}
            
            symptoms_list = symptoms_data.get('primary_symptoms', [])
            if not symptoms_list:
                symptoms_text = "general discomfort"
            elif len(symptoms_list) == 1:
                symptoms_text = symptoms_list[0]
            else:
                symptoms_text = ", ".join(symptoms_list[:-1]) + " and " + symptoms_list[-1]
            
            condition = obs_data.get('likely_condition', 'health concern')
            
            severity_score = symptoms_data.get('severity_rating', 5)
            try:
                score = int(severity_score)
                if score <= 3: severity_text = "mild"
                elif score <= 7: severity_text = "moderate"
                else: severity_text = "high"
            except:
                severity_text = "moderate"

            context_note = ""
            raw_notes = obs_data.get('notes', '')
            if raw_notes:
                clean_note = raw_notes.split('.')[0].strip()
                if clean_note:
                    if clean_note.lower().startswith('patient'):
                        context_note = f", and {clean_note.lower()}" 
                    else:
                        context_note = f" ({clean_note})"

            msg = "📋 *YOUR HEALTH SUMMARY*\n"
            msg += "_(To be reviewed by Doctor)_\n\n"
            msg += f"Hey, looks like you've got a {condition} going on 😷. "
            msg += f"Symptoms include {symptoms_text}{context_note} 🤔. "
            msg += f"Severity is {severity_text} ({severity_score}/10). "
            msg += "The doctor is reviewing your case and will get back to you with a prescription or advice 💊. \n"
            msg += "Anything else to add? Just reply to this message, and the doctor will see it."
            
            return msg

        except Exception as e:
            logger.error(f"Format error: {e}")
            return (
                "Hey, thanks for sharing that info 😷. "
                "The doctor has received your details and is reviewing your case right now. "
                "They'll be back with a prescription or advice shortly 💊. "
                "Anything else to add?"
            )
    
    def _handle_pending_review(self, user, conversation, message_body):
        """Handle messages while assessment is pending clinician review."""
        Message.objects.create(
            conversation=conversation,
            sender=user,
            message_type='PATIENT',
            content=message_body,
            delivery_status='DELIVERED'
        )
        
        if conversation.assigned_clinician:
            try:
                from apps.clinician.whatsapp_handler import ClinicianWhatsAppHandler
                clinician_handler = ClinicianWhatsAppHandler()
                
                clinician_handler.notify_patient_message(
                    conversation.assigned_clinician, 
                    conversation, 
                    f"Patient added: {message_body}"
                )
            except Exception as e:
                logger.error(f"Failed to notify clinician of pending info: {str(e)}")
    
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
            logger.info(f"New message from patient {user.phone_number} for clinician {conversation.assigned_clinician.phone_number}")
            
            try:
                from apps.clinician.whatsapp_handler import ClinicianWhatsAppHandler
                
                clinician_handler = ClinicianWhatsAppHandler()
                clinician_handler.notify_patient_message(
                    conversation.assigned_clinician, 
                    conversation, 
                    message_body
                )
            except Exception as e:
                logger.error(f"Failed to forward message to clinician: {str(e)}")