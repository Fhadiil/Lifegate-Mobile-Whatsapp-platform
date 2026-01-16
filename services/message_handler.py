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
        
        self.incoming_data = incoming_data
        
        try:
            whatsapp_id = incoming_data.get('From')
            # Check if message contains media (voice note)
            message_body = self._normalize_transcription(incoming_data['Body'])
           # message_body = incoming_data.get('Body', '').strip()
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
                
            conversation = self._get_or_create_conversation(user)
            
            # Step 2: Check if payment is required
            if self._handle_package_selection(user, message_body):
                return True
            
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
        
    
    # method to handle package selection
    def _handle_package_selection(self, user, message_body):
        """
        Checks if the user is selecting a payment package (1, 2, 3).
        If yes, sends the payment link and returns True.
        """
        try:
            msg_clean = message_body.strip().lower()
            
            # Only trigger if input is a digit (1, 2, 3)
            if msg_clean.isdigit():
                idx = int(msg_clean) - 1
                packages = list(CreditPackage.objects.all().order_by('price'))
                
                # Check if it matches a valid package index
                if 0 <= idx < len(packages):
                    selected_pkg = packages[idx]
                    self._send_payment_link(user, selected_pkg)
                    return True # Intercepted!
            
            return False # Not a payment selection, continue normal flow
            
        except Exception as e:
            logger.error(f"Package selection error: {e}")
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

    def _normalize_transcription(self, transcription: str) -> str:
        """Normalize transcription text for consistent processing."""
        if not transcription:
            return ""
        # Lowercase
        transcription = transcription.lower()
        # Remove punctuation
        transcription = transcription.translate(str.maketrans('', '', string.punctuation))
        return transcription.strip()


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
                       'AI_TRIAGE_IN_PROGRESS', 'PENDING_PAYMENT', 'PENDING_CLINICIAN_REVIEW', 'DIRECT_MESSAGING']
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
    
    def _check_consultation_payment(self, user, conversation):
        """
        Credit-Based Gatekeeper.
        Returns: True (Allowed), False (Blocked/Sent Menu)
        """
        # 1. Bypass for Staff
        if user.role != 'PATIENT': return True

        # 2. ALREADY PAID? 
        # If this specific conversation was unlocked previously, let them pass.
        if conversation.is_paid:
            return True

        # 3. CHECK WALLET (Deduct Credit)
        profile, _ = PatientProfile.objects.get_or_create(user=user)
        
        if profile.consultation_credits > 0:
            # ✅ HAS CREDITS: Deduct 1 and Unlock
            profile.consultation_credits -= 1
            profile.save()
            
            conversation.is_paid = True
            conversation.save()
            
            # self.twilio.send_message(
            #     user.whatsapp_id, 
            #     f"🎫 *1 Credit Used*\n"
            #     f"Consultation started. Remaining credits: {profile.consultation_credits}"
            # )
            return True

        # 4. ⛔ NO CREDITS: Handle Payment Flow
        
        # Did user select a package number? (e.g. "2")
        packages = list(CreditPackage.objects.all().order_by('price'))
        selected_pkg = None
        
        msg_clean = self.incoming_data.get('Body', '').strip().lower()
        if msg_clean.isdigit():
            idx = int(msg_clean) - 1
            if 0 <= idx < len(packages):
                selected_pkg = packages[idx]

        # 5. If Package Selected -> Send Link
        if selected_pkg:
            self._send_payment_link(user, selected_pkg)
            return False 

        # 6. Default -> Show Menu
        self._send_credit_menu(user, packages)
        return False

    def _send_credit_menu(self, user, packages):
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

    def _send_payment_link(self, user, pkg):
        tx_ref = f"PKG-{user.id}-{uuid.uuid4().hex[:8]}"
        
        # Record pending transaction
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
                
            # HANDLE SHORT INPUT OR EMPTY INPUT 
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
            self.twilio.send_message(user.whatsapp_id, "An error occurred. Please try again.")
            
    def _generate_assessment(self, user, conversation):
        """
        Generate AI assessment.
        If user has credits -> Deduct & Assign Doctor immediately.
        If user has NO credits -> Show 'Locked' summary & Ask for Payment.
        """
        try:
            # 1. Generate Assessment from AI
            assessment_json = self.ai_engine.generate_assessment(conversation)
            
            # 2. Save to Database (Status = PENDING_PAYMENT)
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
            
            # 3. Check Credits to Decide Path
            profile = user.patient_profile
            
            if profile.consultation_credits > 0:
                # ✅ PATH A: HAS CREDITS (Instant Unlock)
                logger.info(f"User {user.phone_number} has credits. Unlocking immediately.")
                
                # First, send the nice summary
                patient_msg = self._format_patient_summary(assessment, conversation)
                self.twilio.send_message(user.whatsapp_id, patient_msg)
                
                # Then, finalize (Deduct credit, Assign Doctor, Update Status)
                # We use the helper to ensure logic matches the payment webhook
                finalize_consultation_flow(user, conversation, assessment)
                
            else:
                # ⛔ PATH B: NO CREDITS (Paywall)
                logger.info(f"User {user.phone_number} has 0 credits. Pausing for payment.")
                
                # Update conversation status to reflect waiting
                # We keep it as AI_TRIAGE_IN_PROGRESS or switch to a holding state
                conversation.save()
                
                # Send Teaser Message
                msg = "✅ *ASSESSMENT COMPLETE*\n\n"
                msg += "We have analyzed your symptoms.\n"
                msg += "To unlock your full results and have a doctor review your case, please use a credit.\n\n"
                msg += "🔒 *Balance: 0 Credits*\n"
                msg += "👇 *Select a package to unlock:*"
                
                self.twilio.send_message(user.whatsapp_id, msg)
                
                # Show Payment Menu
                packages = list(CreditPackage.objects.all().order_by('price'))
                self._send_credit_menu(user, packages)
                
                # We DO NOT assign a clinician yet. 
                # The Webhook/SuccessView will call finalize_consultation_flow() later.

        except Exception as e:
            logger.error(f"Error generating assessment: {str(e)}")
            self.twilio.send_message(user.whatsapp_id, "An error occurred generating your results. Please try again later.")

    def _format_patient_summary(self, assessment, conversation):
        """
        Create a casual, conversational summary for the patient.
        Format: Narrative paragraph with emojis.
        """
        try:
            # 1. Safe Data Extraction
            symptoms_data = assessment.symptoms_overview or {}
            obs_data = assessment.key_observations or {}
            
            # Symptoms list
            symptoms_list = symptoms_data.get('primary_symptoms', [])
            if not symptoms_list:
                symptoms_text = "general discomfort"
            elif len(symptoms_list) == 1:
                symptoms_text = symptoms_list[0]
            else:
                # "headache, fever, and coughing"
                symptoms_text = ", ".join(symptoms_list[:-1]) + " and " + symptoms_list[-1]
            
            # Condition (Lower case unless it's an acronym like flu)
            condition = obs_data.get('likely_condition', 'health concern')
            
            # Severity Text logic
            severity_score = symptoms_data.get('severity_rating', 5)
            try:
                score = int(severity_score)
                if score <= 3: severity_text = "mild"
                elif score <= 7: severity_text = "moderate"
                else: severity_text = "high"
            except:
                severity_text = "moderate"

            # Context/Notes (Extract first sentence of notes if available)
            context_note = ""
            raw_notes = obs_data.get('notes', '')
            if raw_notes:
                # Clean up note: take first sentence, lower case first letter
                clean_note = raw_notes.split('.')[0].strip()
                if clean_note:
                    # check if it starts with 'patient' to avoid awkward grammar
                    if clean_note.lower().startswith('patient'):
                        context_note = f", and {clean_note.lower()}" 
                    else:
                        context_note = f" ({clean_note})"

            # 2. Build the Narrative Message
            # "Hey, looks like you've got a [Condition] going on 😷."
            msg = "📋 *YOUR HEALTH SUMMARY*\n"
            msg += "_(To be reviewed by Doctor)_\n\n"
            msg += f"Hey, looks like you've got a {condition} going on 😷. "
            
            # "Symptoms include [A, B, and C] [Context Note] 🤔."
            msg += f"Symptoms include {symptoms_text}{context_note} 🤔. "
            
            # "Severity is [Level] ([Score]/10)."
            msg += f"Severity is {severity_text} ({severity_score}/10). "
            
            # Closing Standard Text
            msg += "The doctor is reviewing your case and will get back to you with a prescription or advice 💊. \n"
            msg += "Anything else to add? Just reply to this message, and the doctor will see it."
            

            return msg

        except Exception as e:
            logger.error(f"Format error: {e}")
            # Safe Fallback
            return (
                "Hey, thanks for sharing that info 😷. "
                "The doctor has received your details and is reviewing your case right now. "
                "They'll be back with a prescription or advice shortly 💊. "
                "Anything else to add?"
            )
    
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
        
        # Ack to patient
        # self.twilio.send_message(
        #     user.whatsapp_id,
        #     "Your message has been sent to the clinician reviewing your case. They will get back to you soon."
        # )

        # Notify clinician that patient added info
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
        
        # 2. Check if a clinician is assigned
        if conversation.assigned_clinician:
            logger.info(f"New message from patient {user.phone_number} for clinician {conversation.assigned_clinician.phone_number}")
            
            # Forward message to clinician
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
