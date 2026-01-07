import logging
import json
from datetime import datetime, timedelta
from django.utils import timezone
from django.conf import settings
from django.db.models import Q
from apps.conversations.models import ConversationSession, Message
from apps.assessments.models import AIAssessment, AssessmentReview
from apps.clinician.models import ClinicianAvailability, PatientAssignment, ModificationSession
from apps.authentication.models import User, ClinicianProfile
from apps.audit.models import AuditLog
from apps.escalations.models import EscalationAlert
from integrations.twilio.client import TwilioClient
from apps.assessments.validator import AssessmentModificationValidator
from apps.assessments.prescription_generator import PrescriptionPDFGenerator

logger = logging.getLogger('lifegate')


class ClinicianWhatsAppHandler:
      
    def __init__(self):
        self.twilio = TwilioClient()
        self.validator = AssessmentModificationValidator()
        self.pdf_generator = PrescriptionPDFGenerator()
    
    # MAIN ENTRY POINT
    
    def process_clinician_message(self, incoming_data):
        """
        Process incoming WhatsApp message from clinician.
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
                    "Not registered as clinician.\n\n"
                    "If you should have access, contact admin."
                )
                return False
            
            logger.info(f"[CLINICIAN] Found: {clinician.first_name} {clinician.last_name}")
            
            # Check if clinician is in modification session
            mod_session = self._get_active_modification_session(clinician)
            if mod_session:
                return self._handle_modification_workflow(clinician, message_body, mod_session)
            
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
            
            
            if command == 'send_anyway':
                # send_anyway abc-123 session-456
                parts = args.split()
                if len(parts) >= 2:
                    return self._handle_send_anyway(clinician, parts[0], parts[1])
                return False
            
            elif command == 'confirm_send':
                # confirm_send abc-123 session-456
                parts = args.split()
                if len(parts) >= 2:
                    return self._handle_confirm_send(clinician, parts[0], parts[1])
                return False
            
            elif command == 'help':
                self._send_help(clinician)
                return True
            
            elif command == 'pending':
                self._send_pending_assessments(clinician)
                return True
            
            elif command == 'modify':
                return self._start_modify_workflow(clinician, args)
            
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
            
            elif command == 'close' or command == 'discharge':
                return self._handle_close(clinician, args)
            
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
• modify <id> - Modify assessment
• approve <id> - Approve assessment
• reject <id> - Reject assessment
• send <id> - Send to patient
• message <conv_id> <message> - Message patient
• status <available|busy|offline> - Update status
• close <id> - Discharge patient (End Session)

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
                # Append full AI assessment content so clinician can review before approving
                try:
                    detailed = self._format_assessment_for_clinician(assessment, clinician)
                    message += "*FULL ASSESSMENT (review before approving):*\n"
                    message += detailed + "\n\n"
                except Exception:
                    logger.exception("[CLINICIAN] Failed to format detailed assessment")

            message += "👉 *ACTIONS:*\n"
            message += "modify <id> - Modify\n"
            message += "approve <id> - Approve\n"
            message += "reject <id> - Reject\n"
            message += "send <id> - Send to patient"
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Sent pending list to {clinician.phone_number}")
        
        except Exception as e:
            print(f"[CLINICIAN] Error in pending: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "Error loading pending assessments")
            
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
            self._send_to_clinician(clinician, "Error loading escalations")
    
   
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
                message = " *NO ACTIVE PATIENTS*\n\nCheck back soon!"
                self._send_to_clinician(clinician, message)
                return
            
            message = " *YOUR ACTIVE PATIENTS* ({} total)\n\n".format(assignments.count())
            
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
            self._send_to_clinician(clinician, "Error loading patients")
    
    
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
                    "*USAGE:* approve <assessment_id>\n\n"
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
                "Assessment not found\n\n"
                "Check: pending\n"
                "(to see valid IDs)"
            )
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in approve: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "Error approving assessment")
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
                    "*USAGE:* reject <assessment_id>\n\n"
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
            
            message = f"*ASSESSMENT REJECTED*\n\n"
            message += f"Patient: {assessment.patient.phone_number}\n\n"
            message += "Patient will be asked for more information."
            
            self._send_to_clinician(clinician, message)
            
            logger.info(f"[CLINICIAN] Rejected {assessment.id}")
            return True
        
        except AIAssessment.DoesNotExist:
            self.twilio.send_message(
                clinician.whatsapp_id,
                "Assessment not found\n\nCheck: pending"
            )
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in reject: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "Error rejecting assessment")
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
                    "*USAGE:* send <assessment_id>\n\n"
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
            
            # Check if there's a modification session
            mod_session = ModificationSession.objects.filter(
                assessment=assessment,
                clinician=clinician,
                status='COMPLETED'
            ).first()
            
            if mod_session:
                # Validate the modifications before sending
                logger.info(f"[VALIDATOR] Checking modifications for {assessment.id}")
                validation_result = self.validator.validate_modification(assessment, mod_session)
                
                # Store validation result
                mod_session.validation_result = validation_result
                mod_session.save()
                
                # Send validation report
                self._send_validation_report(clinician, validation_result, assessment, mod_session)
                
                # IMPORTANT: Return here - don't send yet!
                return True
            else:
                # No modification - send directly without validation
                logger.info(f"[CLINICIAN] No modifications, sending directly")
                return self._finalize_send_to_patient(clinician, assessment, None)
        
        except AIAssessment.DoesNotExist:
            print(f"[CLINICIAN] Assessment not found: {args}")
            self.twilio.send_message(
                clinician.whatsapp_id,
                "Assessment not found\n\nCheck: pending"
            )
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in send: {str(e)}", exc_info=True)
            self.twilio.send_message(clinician.whatsapp_id, "Error sending assessment")
            return False
    
    # VALIDATION REPORT
    
    def _send_validation_report(self, clinician, validation_result, assessment, mod_session):
        """
        Send validation report to clinician.
        Warns about issues and asks for confirmation/override.
        """
        
        try:
            severity = validation_result['severity']
            recommendation = validation_result['recommendation']
            
            logger.info(f"[VALIDATOR] Severity: {severity}, Recommendation: {recommendation}")
            
            # Send the validation summary
            summary = validation_result['summary']
            self.twilio.send_message(clinician.whatsapp_id, summary)
            
            # Send detailed issues if there are any
            if validation_result['issues']:
                details = self._format_validation_issues(validation_result['issues'])
                self.twilio.send_message(clinician.whatsapp_id, details)
            
            assessment_id = str(assessment.id)[:12]
            session_id = str(mod_session.id)[:12]
            
            # Send action buttons based on severity
            if recommendation == 'DO_NOT_SEND':
                # Critical issues - block send
                message = (
                    "🛑 *CRITICAL ISSUES DETECTED*\n\n"
                    "This assessment has critical safety issues and cannot be sent.\n\n"
                    "Please review and fix before sending."
                )
                
                self.twilio.send_message(clinician.whatsapp_id, message)
                
                # Show options
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    f"👉 *OPTIONS:*\n\n"
                    f"modify {assessment_id} - Review & fix\n"
                    f"pending - Go back to pending\n"
                    f"send_anyway {assessment_id} {session_id} - Send despite warnings"
                )
            
            elif recommendation == 'REVIEW':
                # High/Medium issues - warn but allow override
                message = (
                    "⚠️ *ISSUES DETECTED*\n\n"
                    "There are some concerns with this assessment.\n"
                    "Review recommended, but you can send anyway if needed."
                )
                
                self.twilio.send_message(clinician.whatsapp_id, message)
                
                # Show options
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    f"👉 *OPTIONS:*\n\n"
                    f"send_anyway {assessment_id} {session_id} - Send despite warnings\n"
                    f"modify {assessment_id} - Review & fix\n"
                    f"pending - Go back"
                )
            
            elif recommendation == 'SEND':
                # All good - ask for confirmation
                message = (
                    "✅ *VALIDATION PASSED*\n\n"
                    "Assessment is ready to send to patient."
                )
                
                self.twilio.send_message(clinician.whatsapp_id, message)
                
                # Show confirmation option
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    f"👉 *CONFIRM SEND:*\n\n"
                    f"confirm_send {assessment_id} {session_id}\n\n"
                    f"Or: pending - Go back"
                )
        
        except Exception as e:
            logger.error(f"[VALIDATOR] Error sending report: {str(e)}")
            self.twilio.send_message(clinician.whatsapp_id, "Error validating assessment")
    
    # HANDLE SEND ANYWAY 
    
    def _handle_send_anyway(self, clinician, assessment_id, session_id):
        """
        Clinician has decided to send despite validation warnings.
        """
        
        try:
            assessment = self._get_assessment_by_id(clinician, assessment_id)
            mod_session = self._get_modification_session(clinician, session_id)
            
            if not assessment or not mod_session:
                self.twilio.send_message(clinician.whatsapp_id, "Assessment or session not found")
                return False
            
            logger.warning(
                f"[VALIDATOR] Clinician {clinician.phone_number} OVERRIDING validation for {assessment.id}"
            )
            
            # Mark session as sent with warnings
            mod_session.sent_with_warnings = True
            mod_session.warning_override_reason = 'Clinician chose to send despite validation warnings'
            mod_session.save()
            
            # Create audit log of override
            AuditLog.objects.create(
                user=clinician,
                action_type='ASSESSMENT_OVERRIDE',
                resource_type='AIAssessment',
                resource_id=str(assessment.id),
                description='Clinician overrode validation warnings and sent anyway'
            )
            
            # Send confirmation
            self.twilio.send_message(
                clinician.whatsapp_id,
                "⚠️ *SENDING DESPITE WARNINGS*\n\n"
                "Assessment will be sent to patient.\n"
                "This action has been logged for review."
            )
            
            # Actually send it
            return self._finalize_send_to_patient(clinician, assessment, mod_session)
        
        except Exception as e:
            logger.error(f"[VALIDATOR] Error in send_anyway: {str(e)}")
            self.twilio.send_message(clinician.whatsapp_id, "Error sending assessment")
            return False
    
    # CONFIRM SEND AFTER VALIDATION
    
    def _handle_confirm_send(self, clinician, assessment_id, session_id):
        """
        Clinician confirmed send after validation passed.
        """
        
        try:
            assessment = self._get_assessment_by_id(clinician, assessment_id)
            mod_session = self._get_modification_session(clinician, session_id)
            
            if not assessment or not mod_session:
                self.twilio.send_message(clinician.whatsapp_id, "Assessment or session not found")
                return False
            
            logger.info(f"[VALIDATOR] Confirmed send for {assessment.id}")
            
            # Create audit log of validated send
            AuditLog.objects.create(
                user=clinician,
                action_type='ASSESSMENT_SENT',
                resource_type='AIAssessment',
                resource_id=str(assessment.id),
                description='Sent to patient via WhatsApp (Validation: PASSED)'
            )
            
            # Actually send it
            return self._finalize_send_to_patient(clinician, assessment, mod_session)
        
        except Exception as e:
            logger.error(f"[VALIDATOR] Error in confirm_send: {str(e)}")
            self.twilio.send_message(clinician.whatsapp_id, "Error sending assessment")
            return False
    
    # FINALIZE SEND 
    
    def _finalize_send_to_patient(self, clinician, assessment, mod_session=None):
        """
        Actually send the assessment to patient.
        Uses modified versions if available, otherwise uses original.
        """
        
        try:
            # Get final content (from modification if available)
            if mod_session:
                final_recs = mod_session.modified_recommendations or assessment.preliminary_recommendations or {}
                final_meds = mod_session.modified_otc_suggestions or assessment.otc_suggestions or {}
                final_monitoring = mod_session.modified_monitoring_advice or assessment.monitoring_advice or {}
                notes = mod_session.clinician_notes or ''
            else:
                final_recs = assessment.preliminary_recommendations or {}
                final_meds = assessment.otc_suggestions or {}
                final_monitoring = assessment.monitoring_advice or {}
                notes = ''
            
            # Format assessment message
            formatted_message = self._format_assessment_message_for_patient(
                assessment, clinician, final_recs, final_meds, final_monitoring, notes
            )
            
            pdf_buffer = self.pdf_generator.generate_prescription(
                assessment, 
                clinician, 
                mod_session
            )
            
            pdf_filename = f"prescription_{str(assessment.id)[:8].upper()}.pdf"
            
            pdf_path = self._save_prescription_pdf(assessment, pdf_buffer, pdf_filename)
            
            # Send to patient
            logger.info(f"[CLINICIAN] Sending to patient: {assessment.patient.phone_number}")
            self.twilio.send_message(assessment.patient.whatsapp_id, formatted_message)
            
            self._send_pdf_to_patient(assessment.patient.whatsapp_id, pdf_buffer, pdf_filename)
            
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
            
            self._save_prescription_record(assessment, clinician, mod_session, pdf_path)
            
            message = f"✅ *ASSESSMENT SENT*\n\n"
            message += f"Patient: {assessment.patient.phone_number}\n"
            message += f"Status: SENT\n\n"
            message += f"Patient can now reply with questions."
            
            self.twilio.send_message(clinician.whatsapp_id, message)
            
            logger.info(f"[CLINICIAN] Sent {assessment.id}")
            return True
        
        except Exception as e:
            print(f"[CLINICIAN] Error in finalize_send: {str(e)}", exc_info=True)
            self.twilio.send_message(clinician.whatsapp_id, "Error sending assessment")
            return False
    
    
    # COMMAND: MESSAGE
    
    
    def _handle_message(self, clinician, args):
        """
        Send message to patient (Smart ID Lookup).
        Accepts EITHER Conversation ID OR Assessment ID.
        """
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "*USAGE:* message <id> <text>\n\n"
                    "Example: message abc-123 Hello there"
                )
                return False
            
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "Missing message text.\nUsage: message <id> <text>"
                )
                return False
            
            input_id = parts[0]
            msg_text = parts[1]
            
            conversation = None

            
            # 1. Try finding by Conversation ID
            conversation = ConversationSession.objects.filter(
                id__startswith=input_id,
                assigned_clinician=clinician
            ).first()

            # 2. If not found, try finding by Assessment ID
            if not conversation:
                assessment = AIAssessment.objects.filter(
                    id__startswith=input_id,
                    conversation__assigned_clinician=clinician
                ).first()
                if assessment:
                    conversation = assessment.conversation

            # 3. If still not found, fail
            if not conversation:
                print(f"[CLINICIAN] ID not found: {input_id}")
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    f"Context not found for ID: {input_id}\n"
                    "Check the ID and try again."
                )
                return False


            logger.info(f"[CLINICIAN] Message to {conversation.patient.phone_number}: {msg_text[:50]}")
            
            # Send to patient
            self.twilio.send_message(conversation.patient.whatsapp_id, msg_text)
            
            # Save message
            message_record = Message.objects.create(
                conversation=conversation,
                sender=clinician,
                message_type='CLINICIAN',
                content=msg_text,
                delivery_status='SENT'
            )
            
            # Update conversation timestamp so it stays "Active"
            conversation.updated_at = timezone.now()
            conversation.save()
            
            # Log
            AuditLog.objects.create(
                user=clinician,
                action_type='MESSAGE_SENT',
                resource_type='Message',
                resource_id=str(message_record.id),
                description=f'Message sent via WhatsApp'
            )
            logger.info(f"[CLINICIAN] Message sent to patient {conversation.patient.phone_number}")
            
            return True
        
        except Exception as e:
            print(f"[CLINICIAN] Error in message: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "Error sending message")
            return False
    
   # COMMAND: CLOSE / DISCHARGE

    def _handle_close(self, clinician, args):
        """
        Close/Discharge a patient session.
        Allows the patient to start over with a new complaint.
        """
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "*USAGE:* close <id>\n\n"
                    "Example: close abc-123\n"
                    "(Ends the session so patient can start new)"
                )
                return False

            input_id = args.split()[0]
            conversation = None

            # 1. Try Conversation ID
            conversation = ConversationSession.objects.filter(
                id__startswith=input_id,
                assigned_clinician=clinician
            ).first()

            # 2. Try Assessment ID
            if not conversation:
                assessment = AIAssessment.objects.filter(
                    id__startswith=input_id,
                    conversation__assigned_clinician=clinician
                ).first()
                if assessment:
                    conversation = assessment.conversation

            if not conversation:
                self.twilio.send_message(clinician.whatsapp_id, "❌ Session not found for that ID")
                return False

            
            # 1. Close Conversation
            conversation.status = 'COMPLETED'
            conversation.closed_at = timezone.now()
            conversation.save()

            # 2. Close Patient Assignment (Remove from Active List)
            PatientAssignment.objects.filter(
                conversation=conversation,
                clinician=clinician,
                status='ACTIVE'
            ).update(status='COMPLETED')

            # 3. Update Availability (if needed)
            availability, _ = ClinicianAvailability.objects.get_or_create(
                clinician=clinician
            )
            if availability.status == 'BUSY':
                availability.status = 'AVAILABLE'
                availability.save()

            # 4. Notify Patient
            self.twilio.send_message(
                conversation.patient.whatsapp_id,
                "*SESSION CLOSED*\n\n"
                f"Dr. {clinician.last_name} has closed this consultation.\n\n"
                "If you have a new health concern in the future, just reply *Hi* to start a new assessment. 👋"
            )

            # 5. Notify Clinician
            patient_name = conversation.patient.first_name or "Patient"
            self.twilio.send_message(
                clinician.whatsapp_id,
                f"✅ Session closed for {patient_name}.\n"
                "They have been removed from your active list."
            )
            
            logger.info(f"[CLINICIAN] Discharged patient {conversation.patient.phone_number}")
            return True

        except Exception as e:
            print(f"[CLINICIAN] Error closing session: {str(e)}", exc_info=True)
            self._send_to_clinician(clinician, "Error closing session")
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
                    "*USAGE:* status <available|busy|offline>\n\n"
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
                    "Invalid status\n\n"
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
            self._send_to_clinician(clinician, "Error updating status")
            return False
    
   
    # UNKNOWN COMMAND
   
    
    def _send_unknown_command(self, clinician, command):
        """Handle unknown command."""
        
        message = f"❓ Unknown command: *{command}*\n\n"
        message += "Type *help* to see all commands"
        
        self._send_to_clinician(clinician, message)
        
        logger.warning(f"[CLINICIAN] Unknown command: {command}")
    
    # MODIFICATION WORKFLOW 
    
    def _get_active_modification_session(self, clinician):
        """Get active modification session for clinician."""
        try:
            session = ModificationSession.objects.filter(
                clinician=clinician,
                status='IN_PROGRESS',
                created_at__gte=timezone.now() - timedelta(hours=1)  # Expire after 1 hour
            ).first()
            
            if session and session.is_expired():
                session.status = 'EXPIRED'
                session.save()
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "Modification session expired (older than 1 hour).\n\n"
                    "Start new: modify <assessment_id>"
                )
                return None
            
            return session
        except Exception as e:
            logger.error(f"[CLINICIAN] Error getting modification session: {str(e)}")
            return None
    
    def _start_modify_workflow(self, clinician, args):
        """
        Start interactive modification workflow.
        
        Usage: modify <assessment_id>
        """
        try:
            if not args.strip():
                self.twilio.send_message(
                    clinician.whatsapp_id,
                    "*USAGE:* modify <assessment_id>\n\n"
                    "Example: modify abc-123"
                )
                return False
            
            assessment_id = args.split()[0]
            
            # Find assessment
            assessment = AIAssessment.objects.get(
                id__startswith=assessment_id,
                conversation__assigned_clinician=clinician
            )
            
            # Create modification session
            mod_session = ModificationSession.objects.create(
                clinician=clinician,
                assessment=assessment,
                status='IN_PROGRESS',
                current_step='MEDICATIONS'  # Start with medications
            )
            
            logger.info(f"[CLINICIAN] Started modification session: {mod_session.id}")
            
            # Send initial assessment summary
            summary = self._get_assessment_summary(assessment)
            self.twilio.send_message(
                clinician.whatsapp_id,
                f"📝 *MODIFYING ASSESSMENT*\n\n"
                f"Patient: {assessment.patient.phone_number}\n"
                f"Chief: {assessment.chief_complaint[:50]}\n\n"
                f"{summary}\n\n"
                f"Session ID: {str(mod_session.id)[:12]}"
            )
            
            # Start first step
            self._send_modification_step(clinician, mod_session)
            
            return True
        
        except AIAssessment.DoesNotExist:
            self.twilio.send_message(
                clinician.whatsapp_id,
                "Assessment not found\n\nCheck: pending"
            )
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error starting modify: {str(e)}", exc_info=True)
            self.twilio.send_message(
                clinician.whatsapp_id,
                "Error starting modification"
            )
            return False
    
    def _send_modification_step(self, clinician, mod_session):
        """Send next modification step prompt."""
        assessment = mod_session.assessment
        
        if mod_session.current_step == 'MEDICATIONS':
            message = "💊 *STEP 1: MEDICATIONS*\n\n"
            message += "*Current medications:*\n"
            
            current_meds = assessment.otc_suggestions.get('medications', []) if assessment.otc_suggestions else []
            if current_meds:
                for idx, med in enumerate(current_meds, 1):
                    if isinstance(med, dict):
                        message += f"{idx}. {med.get('name')}: {med.get('dosage')} {med.get('frequency')}\n"
                    else:
                        message += f"{idx}. {med}\n"
            else:
                message += "No medications\n"
            
            message += "\n*Reply with:*\n"
            message += "1️⃣ Keep same\n"
            message += "2️⃣ Add medication (format: name|dosage|frequency)\n"
            message += "3️⃣ Remove all\n"
            message += "4️⃣ Edit (e.g., 'remove 1' or 'add aspirin|500mg|twice daily')\n\n"
            message += "Example: add aspirin|500mg|twice daily"
        
        elif mod_session.current_step == 'RECOMMENDATIONS':
            message = "*STEP 2: RECOMMENDATIONS*\n\n"
            message += "*Current recommendations:*\n"
            
            current_recs = assessment.preliminary_recommendations.get('lifestyle_changes', []) if assessment.preliminary_recommendations else []
            if current_recs:
                for idx, rec in enumerate(current_recs[:5], 1):
                    message += f"{idx}. {rec}\n"
            else:
                message += "No recommendations\n"
            
            message += "\n*Reply with:*\n"
            message += "1️⃣ Keep same\n"
            message += "2️⃣ Add recommendation\n"
            message += "3️⃣ Remove all\n"
            message += "4️⃣ Edit (e.g., 'remove 1' or 'add Get plenty of rest')\n\n"
            message += "Example: add Get plenty of rest"
        
        elif mod_session.current_step == 'MONITORING':
            message = "*STEP 3: WHEN TO SEEK HELP*\n\n"
            message += "*Current guidelines:*\n"
            
            current_when = assessment.monitoring_advice.get('when_to_seek_help', []) if assessment.monitoring_advice else []
            if current_when:
                for idx, item in enumerate(current_when[:5], 1):
                    message += f"{idx}. {item}\n"
            else:
                message += "No guidelines\n"
            
            message += "\n*Reply with:*\n"
            message += "1️⃣ Keep same\n"
            message += "2️⃣ Add guideline\n"
            message += "3️⃣ Remove all\n"
            message += "4️⃣ Edit (e.g., 'remove 1' or 'add Severe fever lasting 3+ days')\n\n"
            message += "Example: add Severe fever lasting 3+ days"
        
        elif mod_session.current_step == 'NOTES':
            message = "📄 *STEP 4: DOCTOR'S NOTE*\n\n"
            message += "Add a personal note for the patient (optional).\n\n"
            message += "*Reply with:*\n"
            message += "1️⃣ Skip (no note)\n"
            message += "2️⃣ Your note\n\n"
            message += "Example: Take medicine with meals and avoid dairy"
        
        elif mod_session.current_step == 'CONFIRM':
            message = "✅ *REVIEW & CONFIRM*\n\n"
            message += self._format_modification_summary(mod_session)
            message += "\n\n*Reply:*\n"
            message += "1️⃣ CONFIRM (send to patient)\n"
            message += "2️⃣ CANCEL (discard changes)"
        
        self.twilio.send_message(clinician.whatsapp_id, message)
    
    def _handle_modification_workflow(self, clinician, response, mod_session):
        """Handle clinician response during modification workflow."""
        try:
            response = response.strip().lower()
            assessment = mod_session.assessment
            
            # Handle different steps
            if mod_session.current_step == 'MEDICATIONS':
                if response == '1':
                    # Keep same
                    if mod_session.modified_otc_suggestions is None:
                        mod_session.modified_otc_suggestions = assessment.otc_suggestions or {'medications': []}
                    mod_session.current_step = 'RECOMMENDATIONS'
                    mod_session.save(update_fields=['modified_otc_suggestions', 'current_step', 'updated_at'])
                    self.twilio.send_message(clinician.whatsapp_id, "✅ Keeping medications as is")
                    self._send_modification_step(clinician, mod_session)
                
                elif response.startswith('2') or response.startswith('add'):
                    # Add medication
                    med_text = response.replace('add', '').strip()
                    
                    # Initialize from original if not already modified
                    if mod_session.modified_otc_suggestions is None:
                        original = assessment.otc_suggestions or {'medications': []}
                        # Deep copy to avoid modifying original
                        mod_session.modified_otc_suggestions = {
                            'medications': list(original.get('medications', []))
                        }
                    
                    parsed_med = self._parse_medication(med_text)
                    if parsed_med:
                        meds = mod_session.modified_otc_suggestions.get('medications', [])
                        meds.append(parsed_med)
                        # Explicitly update the field
                        mod_session.modified_otc_suggestions['medications'] = meds
                        mod_session.save(update_fields=['modified_otc_suggestions', 'updated_at'])
                        self.twilio.send_message(
                            clinician.whatsapp_id,
                            f"✅ Added: {parsed_med.get('name')}\n\n"
                            f"Continue editing or reply '1' to confirm"
                        )
                    else:
                        self.twilio.send_message(
                            clinician.whatsapp_id,
                            "Invalid format\n\n"
                            "Use: add name|dosage|frequency\n"
                            "Example: add aspirin|500mg|twice daily"
                        )
                
                elif response == '3':
                    # Remove all
                    mod_session.modified_otc_suggestions = {'medications': []}
                    mod_session.save(update_fields=['modified_otc_suggestions', 'updated_at'])
                    self.twilio.send_message(clinician.whatsapp_id, "✅ All medications removed")
                    mod_session.current_step = 'RECOMMENDATIONS'
                    mod_session.save(update_fields=['current_step', 'updated_at'])
                    self._send_modification_step(clinician, mod_session)
                
                elif response.startswith('remove'):
                    # Remove specific
                    try:
                        idx = int(response.split()[-1]) - 1
                        if mod_session.modified_otc_suggestions is None:
                            original = assessment.otc_suggestions or {'medications': []}
                            mod_session.modified_otc_suggestions = {
                                'medications': list(original.get('medications', []))
                            }
                        
                        meds = mod_session.modified_otc_suggestions.get('medications', [])
                        if 0 <= idx < len(meds):
                            removed = meds.pop(idx)
                            mod_session.modified_otc_suggestions['medications'] = meds
                            mod_session.save(update_fields=['modified_otc_suggestions', 'updated_at'])
                            self.twilio.send_message(
                                clinician.whatsapp_id,
                                f"✅ Removed medication"
                            )
                        else:
                            self.twilio.send_message(clinician.whatsapp_id, "Invalid index")
                    except:
                        self.twilio.send_message(clinician.whatsapp_id, "Invalid format")
            
            elif mod_session.current_step == 'RECOMMENDATIONS':
                if response == '1':
                    # Keep same
                    if mod_session.modified_recommendations is None:
                        mod_session.modified_recommendations = assessment.preliminary_recommendations or {'lifestyle_changes': []}
                    mod_session.current_step = 'MONITORING'
                    mod_session.save(update_fields=['modified_recommendations', 'current_step', 'updated_at'])
                    self.twilio.send_message(clinician.whatsapp_id, "✅ Keeping recommendations as is")
                    self._send_modification_step(clinician, mod_session)
                
                elif response.startswith('2') or response.startswith('add'):
                    # Add recommendation
                    rec_text = response.replace('add', '').strip()
                    
                    if mod_session.modified_recommendations is None:
                        original = assessment.preliminary_recommendations or {'lifestyle_changes': []}
                        mod_session.modified_recommendations = {
                            'lifestyle_changes': list(original.get('lifestyle_changes', []))
                        }
                    
                    recs = mod_session.modified_recommendations.get('lifestyle_changes', [])
                    recs.append(rec_text)
                    mod_session.modified_recommendations['lifestyle_changes'] = recs
                    mod_session.save(update_fields=['modified_recommendations', 'updated_at'])
                    self.twilio.send_message(clinician.whatsapp_id, f"✅ Added recommendation")
                
                elif response == '3':
                    # Remove all
                    mod_session.modified_recommendations = {'lifestyle_changes': []}
                    mod_session.current_step = 'MONITORING'
                    mod_session.save(update_fields=['modified_recommendations', 'current_step', 'updated_at'])
                    self.twilio.send_message(clinician.whatsapp_id, "✅ All recommendations removed")
                    self._send_modification_step(clinician, mod_session)
                
                elif response.startswith('remove'):
                    # Remove specific
                    try:
                        idx = int(response.split()[-1]) - 1
                        if mod_session.modified_recommendations is None:
                            original = assessment.preliminary_recommendations or {'lifestyle_changes': []}
                            mod_session.modified_recommendations = {
                                'lifestyle_changes': list(original.get('lifestyle_changes', []))
                            }
                        
                        recs = mod_session.modified_recommendations.get('lifestyle_changes', [])
                        if 0 <= idx < len(recs):
                            recs.pop(idx)
                            mod_session.modified_recommendations['lifestyle_changes'] = recs
                            mod_session.save(update_fields=['modified_recommendations', 'updated_at'])
                            self.twilio.send_message(clinician.whatsapp_id, f"✅ Removed recommendation")
                        else:
                            self.twilio.send_message(clinician.whatsapp_id, "Invalid index")
                    except:
                        self.twilio.send_message(clinician.whatsapp_id, "Invalid format")
            
            elif mod_session.current_step == 'MONITORING':
                if response == '1':
                    # Keep same
                    if mod_session.modified_monitoring_advice is None:
                        mod_session.modified_monitoring_advice = assessment.monitoring_advice or {'when_to_seek_help': []}
                    mod_session.current_step = 'NOTES'
                    mod_session.save(update_fields=['modified_monitoring_advice', 'current_step', 'updated_at'])
                    self.twilio.send_message(clinician.whatsapp_id, "✅ Keeping guidelines as is")
                    self._send_modification_step(clinician, mod_session)
                
                elif response.startswith('2') or response.startswith('add'):
                    # Add guideline
                    guide_text = response.replace('add', '').strip()
                    
                    if mod_session.modified_monitoring_advice is None:
                        original = assessment.monitoring_advice or {'when_to_seek_help': []}
                        mod_session.modified_monitoring_advice = {
                            'when_to_seek_help': list(original.get('when_to_seek_help', []))
                        }
                    
                    guides = mod_session.modified_monitoring_advice.get('when_to_seek_help', [])
                    guides.append(guide_text)
                    mod_session.modified_monitoring_advice['when_to_seek_help'] = guides
                    mod_session.save(update_fields=['modified_monitoring_advice', 'updated_at'])
                    self.twilio.send_message(clinician.whatsapp_id, f"✅ Added guideline")
                
                elif response == '3':
                    # Remove all
                    mod_session.modified_monitoring_advice = {'when_to_seek_help': []}
                    mod_session.current_step = 'NOTES'
                    mod_session.save(update_fields=['modified_monitoring_advice', 'current_step', 'updated_at'])
                    self.twilio.send_message(clinician.whatsapp_id, "✅ All guidelines removed")
                    self._send_modification_step(clinician, mod_session)
                
                elif response.startswith('remove'):
                    # Remove specific
                    try:
                        idx = int(response.split()[-1]) - 1
                        if mod_session.modified_monitoring_advice is None:
                            original = assessment.monitoring_advice or {'when_to_seek_help': []}
                            mod_session.modified_monitoring_advice = {
                                'when_to_seek_help': list(original.get('when_to_seek_help', []))
                            }
                        
                        guides = mod_session.modified_monitoring_advice.get('when_to_seek_help', [])
                        if 0 <= idx < len(guides):
                            guides.pop(idx)
                            mod_session.modified_monitoring_advice['when_to_seek_help'] = guides
                            mod_session.save(update_fields=['modified_monitoring_advice', 'updated_at'])
                            self.twilio.send_message(clinician.whatsapp_id, f"✅ Removed guideline")
                        else:
                            self.twilio.send_message(clinician.whatsapp_id, "Invalid index")
                    except:
                        self.twilio.send_message(clinician.whatsapp_id, "Invalid format")
            
            elif mod_session.current_step == 'NOTES':
                if response == '1':
                    # Skip notes
                    mod_session.clinician_notes = ''
                    mod_session.current_step = 'CONFIRM'
                    mod_session.save()
                    self.twilio.send_message(clinician.whatsapp_id, "✅ Skipping doctor's note")
                    self._send_modification_step(clinician, mod_session)
                else:
                    # Save notes
                    mod_session.clinician_notes = response
                    mod_session.current_step = 'CONFIRM'
                    mod_session.save()
                    self.twilio.send_message(clinician.whatsapp_id, "✅ Note saved")
                    self._send_modification_step(clinician, mod_session)
            
            elif mod_session.current_step == 'CONFIRM':
                if response.startswith('1') or response.startswith('confirm'):
                    # Save modifications and create review
                    return self._finalize_modifications(clinician, mod_session)
                
                elif response.startswith('2') or response.startswith('cancel'):
                    # Cancel workflow
                    mod_session.status = 'CANCELLED'
                    mod_session.save()
                    self.twilio.send_message(
                        clinician.whatsapp_id,
                        "Modification cancelled.\n\n"
                        "Changes discarded."
                    )
                    return True
            
            return True
        
        except Exception as e:
            print(f"[CLINICIAN] Error in modification workflow: {str(e)}", exc_info=True)
            self.twilio.send_message(clinician.whatsapp_id, "Error processing response")
            return False
    
    def _finalize_modifications(self, clinician, mod_session):
        """Create assessment review with modifications."""
        try:
            assessment = mod_session.assessment
            
            # Create assessment review
            review = AssessmentReview.objects.create(
                assessment=assessment,
                clinician=clinician,
                action='MODIFIED',
                clinician_notes=mod_session.clinician_notes,
                clinician_risk_level='MODERATE',
                modified_recommendations=mod_session.modified_recommendations,
                modified_otc_suggestions=mod_session.modified_otc_suggestions,
                modified_monitoring_advice=mod_session.modified_monitoring_advice
            )
            
            # Update assessment status
            assessment.status = 'MODIFIED'
            assessment.save()
            
            # Mark session as complete
            mod_session.status = 'COMPLETED'
            mod_session.save()
            
            # Log action
            AuditLog.objects.create(
                user=clinician,
                action_type='ASSESSMENT_MODIFIED',
                resource_type='AIAssessment',
                resource_id=str(assessment.id),
                description='Modified via WhatsApp'
            )
            
            message = f"✅ *ASSESSMENT MODIFIED*\n\n"
            message += f"Patient: {assessment.patient.phone_number}\n"
            message += f"Status: MODIFIED\n\n"
            message += f"👉 Next: send {str(assessment.id)[:12]}\n"
            message += f"(to send modified version to patient)"
            
            self.twilio.send_message(clinician.whatsapp_id, message)
            
            logger.info(f"[CLINICIAN] Finalized modifications for {assessment.id}")
            return True
        
        except Exception as e:
            print(f"[CLINICIAN] Error finalizing: {str(e)}", exc_info=True)
            self.twilio.send_message(clinician.whatsapp_id, "Error saving modifications")
            return False
    
    #  HELPER METHODS
    
    def _get_assessment_summary(self, assessment):
        """Get summary of current assessment."""
        try:
            lines = []
            
            # Symptoms
            symptoms = assessment.symptoms_overview.get('primary_symptoms', []) if assessment.symptoms_overview else []
            if symptoms:
                lines.append(f"*Symptoms:* {', '.join(symptoms[:2])}")
            
            # Condition
            condition = assessment.key_observations.get('likely_condition', '') if assessment.key_observations else ''
            if condition:
                lines.append(f"*Condition:* {condition[:40]}")
            
            # Confidence
            confidence = int(assessment.confidence_score * 100) if assessment.confidence_score else 0
            lines.append(f"*Confidence:* {confidence}%")
            
            return "\n".join(lines)
        except:
            return "Assessment loaded"
    
    def _parse_medication(self, med_text):
        """Parse medication string: name|dosage|frequency"""
        try:
            parts = med_text.split('|')
            if len(parts) >= 3:
                return {
                    'name': parts[0].strip(),
                    'dosage': parts[1].strip(),
                    'frequency': parts[2].strip()
                }
            return None
        except:
            return None
    
    def _format_modification_summary(self, mod_session):
        """Format summary of modifications."""
        lines = ["📋 *MODIFIED ASSESSMENT SUMMARY:*"]
        
        assessment = mod_session.assessment
        
        # Medications
        lines.append("\n💊 *Medications:*")
        meds = mod_session.modified_otc_suggestions.get('medications', []) if mod_session.modified_otc_suggestions else []
        if meds:
            for med in meds[:3]:
                if isinstance(med, dict):
                    lines.append(f"• {med.get('name')}: {med.get('dosage')} {med.get('frequency')}")
                else:
                    lines.append(f"• {med}")
        else:
            lines.append("• None")
        
        # Recommendations
        lines.append("\n *Recommendations:*")
        recs = mod_session.modified_recommendations.get('lifestyle_changes', []) if mod_session.modified_recommendations else []
        if recs:
            for rec in recs[:3]:
                lines.append(f"• {rec}")
        else:
            lines.append("• None")
        
        # Monitoring
        lines.append("\n *When to seek help:*")
        when = mod_session.modified_monitoring_advice.get('when_to_seek_help', []) if mod_session.modified_monitoring_advice else []
        if when:
            for w in when[:3]:
                lines.append(f"• {w}")
        else:
            lines.append("• None")
        
        # Notes
        if mod_session.clinician_notes:
            lines.append(f"\n📄 *Doctor's Note:*\n{mod_session.clinician_notes}")
        
        return "\n".join(lines)
    
    def _get_clinician_by_whatsapp(self, whatsapp_id):
        """Get clinician user by WhatsApp ID."""
        try:
            if not whatsapp_id:
                return None

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
        """Send WhatsApp message to clinician."""
        try:
            to_whatsapp = clinician.whatsapp_id or clinician.phone_number
            self.twilio.send_message(to_whatsapp, message)
        except Exception as e:
            print(f"[CLINICIAN] Error sending: {str(e)}", exc_info=True)
    
    def _format_assessment_message_for_patient(self, assessment, clinician, final_recs, final_meds, final_monitoring, notes):
        """Format assessment as beautiful WhatsApp message for patient."""
        
        try:
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
            message += "\n *WHAT TO DO:*\n"
            recs = final_recs.get('lifestyle_changes', []) if final_recs else []
            for rec in recs[:3]:
                if isinstance(rec, str):
                    message += f"• {rec}\n"
            
            # When to seek help
            message += "\n *SEEK HELP IF:*\n"
            when_help = final_monitoring.get('when_to_seek_help', []) if final_monitoring else []
            for item in when_help[:3]:
                if isinstance(item, str):
                    message += f"• {item}\n"
            
            # Doctor's note
            if notes:
                message += f"\n *DOCTOR'S NOTE:*\n{notes}"
            
            message += "\n\n💬 Reply to ask questions"
            
            return message
        
        except Exception as e:
            print(f"[CLINICIAN] Error formatting message: {str(e)}", exc_info=True)
            return "Assessment sent to patient"
    
    #  VALIDATION ISSUES FORMATTER 
    
    def _format_validation_issues(self, issues):
        """Format validation issues for WhatsApp display."""
        
        try:
            message = "📋 *DETAILED ISSUES:*\n\n"
            
            # Group by severity
            critical = [i for i in issues if i['severity'] == 'CRITICAL']
            high = [i for i in issues if i['severity'] == 'HIGH']
            medium = [i for i in issues if i['severity'] == 'MEDIUM']
            low = [i for i in issues if i['severity'] == 'LOW']
            
            if critical:
                message += "🛑 *CRITICAL:*\n"
                for issue in critical[:2]:
                    message += f"• {issue['message']}\n"
                    message += f"  Fix: {issue['suggestion']}\n\n"
            
            if high:
                message += "⚠️ *HIGH PRIORITY:*\n"
                for issue in high[:2]:
                    message += f"• {issue['message']}\n"
                    message += f"  Tip: {issue['suggestion']}\n\n"
            
            if medium:
                message += "🔶 *WARNINGS:*\n"
                for issue in medium[:2]:
                    message += f"• {issue['message']}\n\n"
            
            if low:
                message += "ℹ️ *NOTES:*\n"
                for issue in low[:1]:
                    message += f"• {issue['message']}\n"
            
            return message
        
        except Exception as e:
            logger.error(f"[VALIDATOR] Error formatting issues: {str(e)}")
            return "Error formatting issues"
    
    #  HELPER METHODS 
    
    def _get_assessment_by_id(self, clinician, assessment_id):
        """Get assessment by partial ID."""
        try:
            return AIAssessment.objects.get(
                id__startswith=assessment_id,
                conversation__assigned_clinician=clinician
            )
        except AIAssessment.DoesNotExist:
            return None
    
    def _get_modification_session(self, clinician, session_id):
        """Get modification session by partial ID."""
        try:
            return ModificationSession.objects.get(
                id__startswith=session_id,
                clinician=clinician,
                status='COMPLETED'
            )
        except ModificationSession.DoesNotExist:
            return None

    def _format_assessment_for_clinician(self, assessment, clinician):
        """Format AI assessment details for clinician review via WhatsApp."""
        try:
            parts = []

            gen_at = getattr(assessment, 'generated_at', None)
            gen_str = gen_at.strftime('%Y-%m-%d %H:%M') if gen_at else 'Unknown'
            confidence = int(assessment.confidence_score * 100) if getattr(assessment, 'confidence_score', None) else 0

            parts.append(f"ID: {str(assessment.id)}")
            parts.append(f"Generated: {gen_str} | Confidence: {confidence}%")
            parts.append(f"Patient: {assessment.patient.first_name or assessment.patient.phone_number}")
            parts.append(f"Chief complaint: {assessment.chief_complaint}")

            # Symptoms
            symptoms = assessment.symptoms_overview or {}
            primary = symptoms.get('primary_symptoms', []) if isinstance(symptoms, dict) else []
            severity = symptoms.get('severity_rating') if isinstance(symptoms, dict) else None
            duration = symptoms.get('duration') if isinstance(symptoms, dict) else None
            if primary:
                parts.append("\nSymptoms:")
                for s in primary:
                    parts.append(f" - {s}")
            if severity is not None or duration:
                parts.append(f"Severity: {severity or 'N/A'} | Duration: {duration or 'N/A'}")

            # Key observations / likely condition
            ko = assessment.key_observations or {}
            likely = ko.get('likely_condition') if isinstance(ko, dict) else None
            differential = ko.get('differential_diagnoses') if isinstance(ko, dict) else None
            if likely:
                parts.append("\nKey observations / Likely condition:")
                parts.append(f"{likely}")
            if differential:
                parts.append("Differential diagnoses:")
                if isinstance(differential, (list, tuple)):
                    for d in differential[:5]:
                        parts.append(f" - {d}")
                else:
                    parts.append(str(differential))

            # Preliminary recommendations
            recs = assessment.preliminary_recommendations or {}
            if recs:
                parts.append("\nPreliminary recommendations:")
                # show structured keys if present
                for k, v in recs.items():
                    if isinstance(v, (list, tuple)):
                        parts.append(f"{k}:")
                        for item in v[:5]:
                            parts.append(f" - {item}")
                    else:
                        parts.append(f"{k}: {v}")

            # OTC / medication suggestions
            meds = assessment.otc_suggestions or {}
            if meds:
                parts.append("\nOTC / medication suggestions:")
                meds_list = meds.get('medications') if isinstance(meds, dict) else meds
                if isinstance(meds_list, (list, tuple)):
                    for m in meds_list[:6]:
                        if isinstance(m, dict):
                            parts.append(f" - {m.get('name', 'med')}: {m.get('dosage','')} {m.get('frequency','')}")
                        else:
                            parts.append(f" - {m}")
                else:
                    parts.append(str(meds_list))

            # Monitoring advice
            mon = assessment.monitoring_advice or {}
            if mon:
                parts.append("\nMonitoring / when to seek help:")
                when = mon.get('when_to_seek_help') if isinstance(mon, dict) else mon
                if isinstance(when, (list, tuple)):
                    for w in when[:6]:
                        parts.append(f" - {w}")
                else:
                    parts.append(str(when))

            # Raw AI payloads for debugging (if present)
            if getattr(assessment, 'raw_ai_output', None):
                try:
                    raw = assessment.raw_ai_output
                    if isinstance(raw, (dict, list)):
                        parts.append('\nAI Raw Output:')
                        parts.append(json.dumps(raw, indent=2)[:800])
                    else:
                        parts.append('\nAI Raw Output:')
                        parts.append(str(raw)[:800])
                except Exception:
                    pass

            # Join with newlines for WhatsApp readability
            return "\n".join(parts)
        except Exception as e:
            logger.exception("[CLINICIAN] Error formatting clinician assessment")
            return "(Unable to render detailed assessment)"
    
   
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
                    "*USAGE:* modify <assessment_id>\n\n"
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
            self.twilio.send_message(clinician.whatsapp_id, "Assessment not found")
            return False
        
        except Exception as e:
            print(f"[CLINICIAN] Error in modify: {str(e)}", exc_info=True)
            self.twilio.send_message(clinician.whatsapp_id, "Error")
            return False
        
    # PDF SENDING METHODS 
    
    def _send_pdf_to_patient(self, patient_whatsapp_id, pdf_buffer, filename):
        """Send PDF document via Twilio."""
        try:
            pdf_buffer.seek(0)
            
            # 1. Save to Django media storage
            from django.core.files.base import ContentFile
            from django.core.files.storage import default_storage
            
            # This saves to /media/prescriptions/filename.pdf
            file_path = f"prescriptions/{filename}"
            
            # If file exists, delete it first to ensure overwrite (optional)
            if default_storage.exists(file_path):
                default_storage.delete(file_path)
                
            saved_path = default_storage.save(file_path, ContentFile(pdf_buffer.read()))
            
            # 2. Construct Public URL (Critical for Twilio)
            from django.conf import settings
            
            # Ensure SITE_URL is set in settings.py (e.g. your Ngrok URL)
            # Do NOT use localhost
            base_url = getattr(settings, 'SITE_URL', '').rstrip('/')
            if not base_url:
                logger.error("[PDF] SITE_URL not set in settings. Cannot send media.")
                return None
                
            pdf_url = f"{base_url}{settings.MEDIA_URL}{saved_path}"
            
            logger.info(f"[PDF] Public URL generated: {pdf_url}")
            
            # 3. Send via Twilio
            message_text = "📄 *Official Prescription*\nPlease present this document at the pharmacy."
            
            self.twilio.send_media_message(
                patient_whatsapp_id,
                pdf_url,
                message_text
            )
            
            return saved_path
            
        except Exception as e:
            logger.error(f"[PDF] Error sending PDF: {str(e)}", exc_info=True)
            self.twilio.send_message(
                patient_whatsapp_id,
                "Note: Digital prescription generated but could not be attached. "
                "Please contact support if needed."
            )
            return None
    
    def _save_prescription_pdf(self, assessment, pdf_buffer, filename):
        """
        Save prescription PDF to media storage for tracking and retrieval.
        """
        
        try:
            from django.core.files.base import ContentFile
            from django.core.files.storage import default_storage
            
            # Reset buffer to start
            pdf_buffer.seek(0)
            
            # Create file path
            file_path = f"prescriptions/{assessment.patient.phone_number}/{filename}"
            
            # Save file
            saved_path = default_storage.save(file_path, ContentFile(pdf_buffer.read()))
            
            logger.info(f"[PDF] Prescription saved: {saved_path}")
            
            return saved_path
        
        except Exception as e:
            logger.error(f"[PDF] Error saving prescription: {str(e)}")
            return None
    
    def _save_prescription_record(self, assessment, clinician, mod_session, pdf_path):
        """
        Save prescription record to database for audit trail.
        """
        
        try:
            # Import Prescription model (we'll create it)
            from apps.assessments.models import Prescription
            
            prescription = Prescription.objects.create(
                assessment=assessment,
                patient=assessment.patient,
                clinician=clinician,
                pdf_file=pdf_path,
                medications=mod_session.modified_otc_suggestions or assessment.otc_suggestions or {},
                recommendations=mod_session.modified_recommendations or assessment.preliminary_recommendations or {},
                warnings=mod_session.modified_monitoring_advice or assessment.monitoring_advice or {},
                status='SENT'
            )
            
            logger.info(f"[PDF] Prescription record created: {prescription.id}")
            
            return prescription
        
        except ImportError:
            logger.warning("[PDF] Prescription model not found - skipping record save")
            return None
        except Exception as e:
            logger.error(f"[PDF] Error saving prescription record: {str(e)}")
            return None