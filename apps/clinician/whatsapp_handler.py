import logging
import json
from datetime import datetime
from django.utils import timezone
from django.conf import settings
from django.db.models import Q
from apps.conversations.models import ConversationSession, Message
from apps.assessments.models import AIAssessment, AssessmentReview
from apps.clinician.models import ClinicianAvailability, PatientAssignment
from apps.authentication.models import User, ClinicianProfile
from apps.audit.models import AuditLog
from apps.escalations.models import EscalationAlert
from integrations.twilio.client import TwilioClient

logger = logging.getLogger('lifegate')


class ClinicianWhatsAppHandler:
    """
    Complete working handler for clinician WhatsApp messages.
    Handles all commands: pending, approve, send, message, etc.
    """
    
    def __init__(self):
        self.twilio = TwilioClient()
    
    # MAIN ENTRY POINT
    
    def process_clinician_message(self, incoming_data):
        """
        Process incoming WhatsApp message from clinician.
        
        Args:
            incoming_data (dict): {
                'From': 'whatsapp:+1234567890',
                'Body': 'pending',
                'MessageSid': 'SM...'
            }
        
        Returns:
            bool: True if processed successfully
        """
        try:
            whatsapp_id = incoming_data.get('From')
            message_body = incoming_data.get('Body', '').strip()
            
            logger.info(f"[CLINICIAN] WhatsApp from {whatsapp_id}: {message_body[:50]}")
            
            # Get clinician user
            clinician = self._get_clinician_by_whatsapp(whatsapp_id)
            
            if not clinician:
                logger.warning(f"[CLINICIAN] Clinician not found: {whatsapp_id}")
                self.twilio.send_message(
                    whatsapp_id,
                    "❌ Not registered as clinician.\n\n"
                    "If you should have access, contact admin."
                )
                return False
            
            logger.info(f"[CLINICIAN] Found: {clinician.first_name} {clinician.last_name}")
            
            # Log incoming message
            AuditLog.objects.create(
                user=clinician,
                action_type='MESSAGE_RECEIVED',
                resource_type='ClinicianWhatsAppMessage',
                resource_id='',
                description=f"WhatsApp: {message_body[:100]}",
                ip_address='WHATSAPP'
            )
            
            # Parse command (first word)
            command_parts = message_body.split(maxsplit=1)
            command = command_parts[0].lower() if command_parts else ''
            args = command_parts[1] if len(command_parts) > 1 else ''
            
            logger.info(f"[CLINICIAN] Command: {command} | Args: {args}")
            
            # Route to handler
            if command == 'help':
                self._send_help(clinician)
                return True
            
            elif command == 'pending':
                self._send_pending_assessments(clinician)
                return True
            
            elif command == 'modify':
                return self._handle_modify(clinician, args)
            
            elif command == 'escalations':
                self._send_escalations(clinician)
                return True
            
            elif command == 'patients':
                self._send_active_patients(clinician)
                return True
            
            elif command == 'approve':
                return self._handle_approve(clinician, args)
            
            elif command == 'reject':
                return self._handle_reject(clinician, args)
            
            elif command == 'send':
                return self._handle_send(clinician, args)
            
            elif command == 'message':
                return self._handle_message(clinician, args)
            
            elif command == 'status':
                return self._handle_status(clinician, args)
            
            else:
                self._send_unknown_command(clinician, command)
                return True
            
        except Exception as e:
            print(f"[CLINICIAN] Error: {str(e)}", exc_info=True)
            return False
    
   
    # COMMAND: HELP
   
    
    def _send_help(self, clinician):
        """Send help message with all commands."""
        
        message = """📋 *LIFEGATE CLINICIAN COMMANDS*

*VIEW INFORMATION:*
• pending - Show pending assessments
• escalations - Show emergency alerts
• patients - List your active patients

*TAKE ACTIONS:*
• approve <id> - Approve assessment
• reject <id> - Reject assessment
• send <id> - Send to patient
• message <conv_id> <message> - Message patient
• status <available|busy|offline> - Update status

*EXAMPLES:*
• pending
• approve abc-123
• send abc-123
• message conv-xyz When is your pain?
• status available

Type *help* anytime for this message."""
        
        self._send_to_clinician(clinician, message)
        
        logger.info(f"[CLINICIAN] Sent help to {clinician.phone_number}")
    
    # COMMAND: PENDING
   
    
    def _send_pending_assessments(self, clinician):
        """Send list of pending assessments assigned to clinician."""
        
        try:
            qs = AIAssessment.objects.filter(
                conversation__assigned_clinician=clinician,
                status__in=['GENERATED', 'PENDING_REVIEW']
            ).select_related('patient', 'conversation').order_by('-generated_at')

            total = qs.count()
            logger.info(f"[CLINICIAN] Found {total} pending for {clinician.phone_number}")

            if total == 0:
                message = "✅ *NO PENDING ASSESSMENTS*\n\nGreat work! No pending reviews."
                self._send_to_clinician(clinician, message)
                return

            message = "📋 *PENDING ASSESSMENTS* ({} total)\n\n".format(total)

            pending = qs[:5]

            for idx, assessment in enumerate(pending, 1):
                severity = assessment.symptoms_overview.get('severity_rating', 5) if assessment.symptoms_overview else 5
                confidence = int(assessment.confidence_score * 100) if assessment.confidence_score else 0
                
                severity_emoji = '🔴' if severity >= 8 else '🟡' if severity >= 5 else '🟢'
                
                patient_name = assessment.patient.first_name or assessment.patient.phone_number
                chief = assessment.chief_complaint[:40] + '...' if len(assessment.chief_complaint) > 40 else assessment.chief_complaint
                
                message += f"{idx}. *{patient_name}*\n"
                message += f"   {chief}\n"
                message += f"   {severity_emoji} Severity: {severity}/10 | Confidence: {confidence}%\n"
                message += f"   ID: {str(assessment.id)[:12]}\n\n"
            
            message += "👉 *ACTIONS:*\n"
            message += "approve <id> - Approve\n"
            message += "reject <id> - Reject\n"
            message += "send <id> - Send to patient"
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Sent pending list to {clinician.phone_number}")
        
        except Exception as e:
            print(f"[CLINICIAN] Error in pending: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "❌ Error loading pending assessments")
    
   
    # COMMAND: ESCALATIONS
    
    
    def _send_escalations(self, clinician):
        """Send emergency escalations."""
        
        try:
            escalations = EscalationAlert.objects.filter(
                conversation__assigned_clinician=clinician,
                alert_status__in=['PENDING', 'ACKNOWLEDGED']
            ).order_by('-triggered_at')[:5]
            
            if not escalations:
                message = "✅ *NO ESCALATIONS*\n\nAll patients are stable."
                self._send_to_clinician(clinician, message)
                return
            
            message = "🚨 *EMERGENCY ALERTS* ({} total)\n\n".format(escalations.count())
            
            for escalation in escalations:
                severity_emoji = '🔴' if escalation.alert_severity == 'CRITICAL' else '🟠'
                
                message += f"{severity_emoji} *{escalation.conversation.patient.phone_number}*\n"
                message += f"   {escalation.alert_message[:60]}\n"
                message += f"   Status: {escalation.alert_status}\n\n"
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Sent escalations to {clinician.phone_number}")
        
        except Exception as e:
            print(f"[CLINICIAN] Error in escalations: {str(e)}")
            self._send_to_clinician(clinician, "❌ Error loading escalations")
    
   
    # COMMAND: PATIENTS
   
    
    def _send_active_patients(self, clinician):
        """Send list of active patients."""
        
        try:
            assignments = PatientAssignment.objects.filter(
                clinician=clinician,
                status='ACTIVE'
            ).select_related(
                'patient',
                'conversation'
            ).order_by('-assigned_at')[:10]
            
            if not assignments:
                message = "👥 *NO ACTIVE PATIENTS*\n\nCheck back soon!"
                self._send_to_clinician(clinician, message)
                return
            
            message = "👥 *YOUR ACTIVE PATIENTS* ({} total)\n\n".format(assignments.count())
            
            for idx, assignment in enumerate(assignments, 1):
                patient_name = assignment.patient.first_name or assignment.patient.phone_number
                chief = assignment.conversation.chief_complaint[:35] + '...' if len(assignment.conversation.chief_complaint) > 35 else assignment.conversation.chief_complaint
                
                message += f"{idx}. *{patient_name}*\n"
                message += f"   {chief}\n"
                message += f"   Assigned: {assignment.assigned_at.strftime('%H:%M')}\n\n"
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Sent patients to {clinician.phone_number}")
        
        except Exception as e:
            print(f"[CLINICIAN] Error in patients: {str(e)}")
            self._send_to_clinician(clinician, "❌ Error loading patients")
    
    
    # COMMAND: APPROVE
   
    
    def _handle_approve(self, clinician, args):
        """
        Approve assessment.
        
        Usage: approve <assessment_id>
        Example: approve abc-123
        """
        
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "❌ *USAGE:* approve <assessment_id>\n\n"
                    "Example: approve abc-123\n"
                    "(Use first 12 chars of ID from pending)"
                )
                return False
            
            assessment_id = args.split()[0]
            
            logger.info(f"[CLINICIAN] Approve: {assessment_id}")
            
            # Find assessment
            assessment = AIAssessment.objects.get(
                id__startswith=assessment_id,
                conversation__assigned_clinician=clinician
            )
            
            logger.info(f"[CLINICIAN] Found assessment: {assessment.id}")
            
            # Create review
            review = AssessmentReview.objects.create(
                assessment=assessment,
                clinician=clinician,
                action='APPROVED',
                clinician_notes='Approved via WhatsApp',
                clinician_risk_level='MODERATE'
            )
            
            # Update assessment
            assessment.status = 'APPROVED'
            assessment.save()
            
            # Log
            AuditLog.objects.create(
                user=clinician,
                action_type='ASSESSMENT_APPROVED',
                resource_type='AIAssessment',
                resource_id=str(assessment.id),
                description=f'Approved via WhatsApp'
            )
            
            message = f"✅ *ASSESSMENT APPROVED*\n\n"
            message += f"Patient: {assessment.patient.phone_number}\n"
            message += f"Chief: {assessment.chief_complaint[:50]}\n\n"
            message += f"👉 Next: send {str(assessment.id)[:12]}\n"
            message += f"(This sends assessment to patient)"
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Approved {assessment.id}")
            return True
        
        except AIAssessment.DoesNotExist:
            print(f"[CLINICIAN] Assessment not found: {args}")
            self.twilio.send_message(
                clinician.whatsapp_id,
                "❌ Assessment not found\n\n"
                "Check: pending\n"
                "(to see valid IDs)"
            )
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in approve: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "❌ Error approving assessment")
            return False
    
    
    # COMMAND: REJECT
   
    
    def _handle_reject(self, clinician, args):
        """
        Reject assessment.
        
        Usage: reject <assessment_id>
        """
        
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "❌ *USAGE:* reject <assessment_id>\n\n"
                    "Example: reject abc-123"
                )
                return False
            
            assessment_id = args.split()[0]
            
            # Find assessment
            assessment = AIAssessment.objects.get(
                id__startswith=assessment_id,
                conversation__assigned_clinician=clinician
            )
            
            # Create review
            review = AssessmentReview.objects.create(
                assessment=assessment,
                clinician=clinician,
                action='REJECTED',
                clinician_notes='Rejected via WhatsApp - needs more info',
                clinician_risk_level='MODERATE'
            )
            
            # Update assessment
            assessment.status = 'REJECTED'
            assessment.save()
            
            # Log
            AuditLog.objects.create(
                user=clinician,
                action_type='ASSESSMENT_REVIEWED',
                resource_type='AIAssessment',
                resource_id=str(assessment.id),
                description=f'Rejected via WhatsApp'
            )
            
            message = f"❌ *ASSESSMENT REJECTED*\n\n"
            message += f"Patient: {assessment.patient.phone_number}\n\n"
            message += "Patient will be asked for more information."
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Rejected {assessment.id}")
            return True
        
        except AIAssessment.DoesNotExist:
            self.twilio.send_message(
                clinician.whatsapp_id,
                "❌ Assessment not found\n\nCheck: pending"
            )
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in reject: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "❌ Error rejecting assessment")
            return False
    
   
    # COMMAND: SEND
    
    
    def _handle_send(self, clinician, args):
        """
        Send approved assessment to patient.
        
        Usage: send <assessment_id>
        """
        
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "❌ *USAGE:* send <assessment_id>\n\n"
                    "Example: send abc-123"
                )
                return False
            
            assessment_id = args.split()[0]
            
            logger.info(f"[CLINICIAN] Send: {assessment_id}")
            
            # Find assessment
            assessment = AIAssessment.objects.get(
                id__startswith=assessment_id,
                conversation__assigned_clinician=clinician
            )
            
            # Check status
            if assessment.status not in ['APPROVED', 'MODIFIED']:
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    f"⚠️ Assessment must be approved first\n\n"
                    f"Use: approve {str(assessment.id)[:12]}"
                )
                return False
            
            # Format assessment message
            formatted_message = self._format_assessment_message(assessment, clinician)
            
            # Send to patient
            logger.info(f"[CLINICIAN] Sending to patient: {assessment.patient.phone_number}")
            self.twilio.send_message(assessment.patient.whatsapp_id, formatted_message)
            
            # Update assessment
            assessment.status = 'SENT_TO_PATIENT'
            assessment.sent_to_patient_at = timezone.now()
            assessment.save()
            
            # Update conversation
            conversation = assessment.conversation
            conversation.status = 'DIRECT_MESSAGING'
            conversation.first_clinician_response_at = timezone.now()
            conversation.save()
            
            # Save message record
            Message.objects.create(
                conversation=conversation,
                sender=clinician,
                message_type='CLINICIAN',
                content=formatted_message,
                delivery_status='SENT'
            )
            
            # Log
            AuditLog.objects.create(
                user=clinician,
                action_type='ASSESSMENT_SENT',
                resource_type='AIAssessment',
                resource_id=str(assessment.id),
                description=f'Sent to patient via WhatsApp'
            )
            
            message = f"✅ *ASSESSMENT SENT*\n\n"
            message += f"Patient: {assessment.patient.phone_number}\n"
            message += f"Status: SENT\n\n"
            message += f"Patient can now reply with questions."
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Sent {assessment.id}")
            return True
        
        except AIAssessment.DoesNotExist:
            print(f"[CLINICIAN] Assessment not found: {args}")
            self.twilio.send_message(
                clinician.whatsapp_id,
                "❌ Assessment not found\n\nCheck: pending"
            )
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in send: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "❌ Error sending assessment")
            return False
    
    
    # COMMAND: MESSAGE
    
    
    def _handle_message(self, clinician, args):
        """
        Send message to patient.
        
        Usage: message <conversation_id> <message>
        Example: message conv-abc Hello, how are you?
        """
        
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "❌ *USAGE:* message <conv_id> <message>\n\n"
                    "Example: message conv-abc Hello"
                )
                return False
            
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "❌ *USAGE:* message <conv_id> <message>"
                )
                return False
            
            conv_id = parts[0]
            msg_text = parts[1]
            
            logger.info(f"[CLINICIAN] Message to {conv_id}: {msg_text[:50]}")
            
            # Find conversation
            conversation = ConversationSession.objects.get(
                id__startswith=conv_id,
                assigned_clinician=clinician
            )
            
            # Send to patient
            logger.info(f"[CLINICIAN] Sending to {conversation.patient.phone_number}")
            self.twilio.send_message(conversation.patient.whatsapp_id, msg_text)
            
            # Save message
            message_record = Message.objects.create(
                conversation=conversation,
                sender=clinician,
                message_type='CLINICIAN',
                content=msg_text,
                delivery_status='SENT'
            )
            
            # Log
            AuditLog.objects.create(
                user=clinician,
                action_type='MESSAGE_SENT',
                resource_type='Message',
                resource_id=str(message_record.id),
                description=f'Message sent via WhatsApp'
            )
            
            self.twilio.send_message(
                clinician.whatsapp_id,
                "✅ *MESSAGE SENT*"
            )
            
            logger.info(f"[CLINICIAN] Message sent")
            return True
        
        except ConversationSession.DoesNotExist:
            print(f"[CLINICIAN] Conversation not found: {args}")
            self.twilio.send_message(
                clinician.whatsapp_id,
                "❌ Conversation not found"
            )
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in message: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "❌ Error sending message")
            return False
    
   
    # COMMAND: STATUS
    
    
    def _handle_status(self, clinician, args):
        """
        Update clinician availability status.
        
        Usage: status <available|busy|offline>
        """
        
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "❌ *USAGE:* status <available|busy|offline>\n\n"
                    "Examples:\n"
                    "status available\n"
                    "status busy\n"
                    "status offline"
                )
                return False
            
            new_status = args.split()[0].upper()
            
            if new_status not in ['AVAILABLE', 'BUSY', 'OFFLINE']:
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "❌ Invalid status\n\n"
                    "Use: available, busy, or offline"
                )
                return False
            
            # Update availability
            availability, _ = ClinicianAvailability.objects.get_or_create(
                clinician=clinician
            )
            availability.status = new_status
            availability.save()
            
            # Log
            AuditLog.objects.create(
                user=clinician,
                action_type='AVAILABILITY_UPDATED',
                resource_type='ClinicianAvailability',
                resource_id=str(availability.id),
                description=f'Status: {new_status}'
            )
            
            status_emoji = {
                'AVAILABLE': '🟢',
                'BUSY': '🟡',
                'OFFLINE': '⚫'
            }
            
            self.twilio.send_message(
                clinician.whatsapp_id,
                f"{status_emoji.get(new_status, '❓')} Status: *{new_status}*"
            )
            
            logger.info(f"[CLINICIAN] Status updated to {new_status}")
            return True
        
        except Exception as e:
            print(f"[CLINICIAN] Error in status: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "❌ Error updating status")
            return False
    
   
    # UNKNOWN COMMAND
   
    
    def _send_unknown_command(self, clinician, command):
        """Handle unknown command."""
        
        message = f"❓ Unknown command: *{command}*\n\n"
        message += "Type *help* to see all commands"
        
        self._send_to_clinician(clinician, message)
        
        logger.warning(f"[CLINICIAN] Unknown command: {command}")
    
   
    # HELPER METHODS
    
    
    def _get_clinician_by_whatsapp(self, whatsapp_id):
        """Get clinician user by WhatsApp ID."""
        try:
            if not whatsapp_id:
                return None

            # Normalize incoming id: accept both 'whatsapp:+123' and '+123'
            raw = whatsapp_id[9:] if whatsapp_id.startswith('whatsapp:') else whatsapp_id

            user = User.objects.filter(role='CLINICIAN').filter(
                Q(whatsapp_id=whatsapp_id) | Q(whatsapp_id=raw) | Q(phone_number=raw)
            ).first()

            if not user:
                logger.warning(f"[CLINICIAN] Not found: {whatsapp_id}")
            return user
        except Exception as e:
            print(f"[CLINICIAN] Error looking up clinician: {str(e)}")
            return None

    def _send_to_clinician(self, clinician, message):
        """Send WhatsApp message to clinician, falling back to phone number."""
        try:
            to_whatsapp = clinician.whatsapp_id or clinician.phone_number
            self.twilio.send_message(to_whatsapp, message)
        except Exception as e:
            print(f"[CLINICIAN] Error sending to clinician: {str(e)}", exc_info=True)
    
    def _format_assessment_message(self, assessment, clinician):
        """Format assessment as beautiful WhatsApp message."""
        
        try:
            # Get final recommendations (from review if modified)
            review = assessment.reviews.filter(
                action__in=['APPROVED', 'MODIFIED']
            ).first()
            
            if review and review.action == 'MODIFIED':
                final_recs = review.modified_recommendations or assessment.preliminary_recommendations
                final_meds = review.modified_otc_suggestions or assessment.otc_suggestions
                final_monitoring = review.modified_monitoring_advice or assessment.monitoring_advice
                notes = review.clinician_notes or ''
            else:
                final_recs = assessment.preliminary_recommendations or {}
                final_meds = assessment.otc_suggestions or {}
                final_monitoring = assessment.monitoring_advice or {}
                notes = ''
            
            symptoms = assessment.symptoms_overview.get('primary_symptoms', []) if assessment.symptoms_overview else []
            condition = assessment.key_observations.get('likely_condition', 'Assessment complete') if assessment.key_observations else 'Assessment complete'
            severity = assessment.symptoms_overview.get('severity_rating', 5) if assessment.symptoms_overview else 5
            duration = assessment.symptoms_overview.get('duration', 'Unknown') if assessment.symptoms_overview else 'Unknown'
            
            message = "✅ *Assessment Complete*\n\n"
            message += f"Hi {assessment.patient.first_name or 'Patient'},\n\n"
            message += f"Dr. {clinician.last_name} has reviewed your assessment.\n\n"
            
            # Symptoms
            message += "📋 *YOUR SYMPTOMS:*\n"
            for symptom in symptoms[:3]:
                message += f"• {symptom}\n"
            message += f"Severity: {severity}/10 | Duration: {duration}\n\n"
            
            # Likely cause
            message += f"💡 *LIKELY CAUSE:*\n{condition}\n\n"
            
            # Medications
            message += "💊 *MEDICATIONS:*\n"
            medications = final_meds.get('medications', []) if final_meds else []
            for med in medications[:2]:
                if isinstance(med, dict):
                    message += f"• {med.get('name', 'Medicine')}: {med.get('dosage', '')} {med.get('frequency', '')}\n"
                else:
                    message += f"• {med}\n"
            
            # Recommendations
            message += "\n🎯 *WHAT TO DO:*\n"
            recs = final_recs.get('lifestyle_changes', []) if final_recs else []
            for rec in recs[:3]:
                if isinstance(rec, str):
                    message += f"• {rec}\n"
            
            # When to seek help
            message += "\n⚠️ *SEEK HELP IF:*\n"
            when_help = final_monitoring.get('when_to_seek_help', []) if final_monitoring else []
            for item in when_help[:3]:
                if isinstance(item, str):
                    message += f"• {item}\n"
            
            # Doctor's note
            if notes:
                message += f"\n👨‍⚕️ *DOCTOR'S NOTE:*\n{notes}"
            
            message += "\n\n💬 Reply to ask questions"
            
            return message
        
        except Exception as e:
            print(f"[CLINICIAN] Error formatting message: {str(e)}", exc_info=True)
            return "Assessment sent to patient"
    
   
    # NOTIFICATION METHODS
   
    
    def notify_new_patient(self, clinician, conversation):
        """Notify clinician about new patient assignment"""
    
        try:
            patient = conversation.patient
            
            message = f"🆕 *NEW PATIENT ASSIGNMENT*\n\n"
            message += f"Patient: {patient.phone_number}\n"
            message += f"Name: {patient.first_name or 'N/A'} {patient.last_name or 'N/A'}\n"
            message += f"Chief Complaint: {conversation.chief_complaint[:60]}\n\n"
            message += "👉 *ACTIONS:*\n"
            message += "pending - View pending assessments\n"
            message += "approve <id> - Approve assessment\n"
            message += "send <id> - Send to patient"
            
            self.twilio.send_message(clinician.whatsapp_id, message)
            
            logger.info(f"[CLINICIAN] Notified new patient: {clinician.phone_number}")
        
        except Exception as e:
            print(f"[CLINICIAN] Error notifying: {str(e)}", exc_info=True)


    def notify_patient_message(self, clinician, conversation, patient_message):
        """Notify clinician when patient sends a message"""
        
        try:
            patient = conversation.patient
            
            message = f"💬 *NEW MESSAGE FROM PATIENT*\n\n"
            message += f"Patient: {patient.phone_number}\n"
            message += f"Name: {patient.first_name or 'N/A'}\n\n"
            message += f"Message:\n\"{patient_message}\"\n\n"
            message += "👉 *REPLY VIA WHATSAPP:*\n"
            message += f"message {str(conversation.id)[:12]} <your message>\n\n"
            message += "Example:\n"
            message += "message abc-123 Take medicine with food"
            
            self.twilio.send_message(clinician.whatsapp_id, message)
            
            logger.info(f"[CLINICIAN] Notified about patient message: {clinician.phone_number}")
        
        except Exception as e:
            print(f"[CLINICIAN] Error notifying: {str(e)}", exc_info=True)

    
    def notify_escalation(self, clinician, escalation):
        """Notify clinician about emergency escalation."""
        
        try:
            conversation = escalation.conversation
            patient = conversation.patient
            
            message = f"🚨 *EMERGENCY ESCALATION*\n\n"
            message += f"Patient: {patient.phone_number}\n"
            message += f"Alert: {escalation.alert_message[:60]}\n"
            message += f"Severity: {escalation.alert_severity}\n\n"
            message += "Please respond immediately."
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Notified escalation: {clinician.phone_number}")
        
        except Exception as e:
            print(f"[CLINICIAN] Error notifying escalation: {str(e)}")
            
    def _handle_modify(self, clinician, args):
        """
        MODIFY ASSESSMENT - Interactive modification workflow
        
        Usage: modify <assessment_id>
        
        Then responds to prompts:
        1. Ask for modified recommendations
        2. Ask for modified medications
        3. Ask for clinician notes
        4. Creates modification and auto-approves
        """
        
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "❌ *USAGE:* modify <assessment_id>\n\n"
                    "Example: modify abc-123"
                )
                return False
            
            assessment_id = args.split()[0]
            
            logger.info(f"[CLINICIAN] Modify: {assessment_id}")
            
            # Find assessment
            assessment = AIAssessment.objects.get(
                id__startswith=assessment_id,
                conversation__assigned_clinician=clinician
            )
            
            # Store in session (using conversation.id as key for now)
            # For now, show the modification UI on dashboard instead
            
            message = f"📝 *MODIFY ASSESSMENT*\n\n"
            message += f"Patient: {assessment.patient.phone_number}\n"
            message += f"Chief: {assessment.chief_complaint[:50]}\n\n"
            message += "⚠️ For detailed modifications, use the WEB DASHBOARD:\n"
            message += "👉 http://localhost:3000/\n\n"
            message += "On dashboard:\n"
            message += "1. Click 'Review' on this assessment\n"
            message += "2. Click 'MODIFY'\n"
            message += "3. Edit recommendations & medications\n"
            message += "4. Submit\n\n"
            message += "Or use WhatsApp for quick approval:\n"
            message += f"approve {str(assessment.id)[:12]}"
            
            self.twilio.send_message(clinician.whatsapp_id, message)
            
            logger.info(f"[CLINICIAN] Sent modify instructions")
            return True
        
        except AIAssessment.DoesNotExist:
            self.twilio.send_message(clinician.whatsapp_id, "❌ Assessment not found")
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in modify: {str(e)}", exc_info=True)
            self.twilio.send_message(clinician.whatsapp_id, "❌ Error")
            return False