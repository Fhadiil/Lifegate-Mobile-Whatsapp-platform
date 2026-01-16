import logging
from django.utils import timezone
from apps.authentication.models import PatientProfile
from apps.conversations.models import Message
from apps.clinician.models import ClinicianAvailability, PatientAssignment
from apps.clinician.whatsapp_handler import ClinicianWhatsAppHandler
from integrations.twilio.client import TwilioClient

logger = logging.getLogger('lifegate')

def finalize_consultation_flow(user, conversation, assessment):
    """
    Deducts credit, assigns clinician, and sends the summary to the patient.
    Called when payment is confirmed (either immediately or after webhook).
    """
    try:
        profile = user.patient_profile
        twilio = TwilioClient()

        # 1. Deduct Credit
        if profile.consultation_credits > 0:
            profile.consultation_credits -= 1
            profile.save()
        else:
            # Safety check: Should not happen if called correctly, but handle gracefully
            logger.warning(f"User {user.phone_number} has 0 credits in finalize flow.")
            return False

        # 2. Update Status
        assessment.status = 'PENDING_REVIEW'
        assessment.save()
        
        conversation.status = 'PENDING_CLINICIAN_REVIEW'
        conversation.triage_completed_at = timezone.now()
        conversation.is_paid = True # Lock is effectively open
        conversation.save()

        # 3. Assign Clinician
        _assign_clinician(conversation)
        
        # 4. GENERATE & SEND SUMMARY
        
        
        # Get condition
        condition = assessment.key_observations.get('likely_condition', 'Health Concern')
        
        # Get symptoms
        symptoms = assessment.symptoms_overview.get('primary_symptoms', [])
        symptoms_text = ", ".join(symptoms) if symptoms else "reported symptoms"
        
        # Get Severity
        severity = assessment.symptoms_overview.get('severity_rating', 5)

        msg = "✅ *PAYMENT CONFIRMED*\n\n"
        msg += "📋 *YOUR HEALTH SUMMARY*\n"
        msg += f"Hey, your results are ready! Looks like it might be *{condition}* 😷.\n"
        msg += f"Symptoms include {symptoms_text}."
        msg += f"Severity is {severity}/10.\n"
        msg += "A doctor is reviewing your case and will get back to you with a prescription or advice. \n"
        msg += "👨‍⚕️ *A doctor has been assigned to your case.*\n"
        msg += "They will review this summary and message you shortly.\n\n"
        msg += "_(You can reply to this message if you want to add any extra details for the doctor)_"
        
        twilio.send_message(user.whatsapp_id, msg)
        
        return True

    except Exception as e:
        logger.error(f"Finalize Flow Error: {e}")
        return False

def _assign_clinician(conversation):
    """Internal helper to find and assign a doctor."""
    available = ClinicianAvailability.objects.filter(
        status__in=['AVAILABLE', 'ON_CALL']
    ).order_by('current_patient_count')[:1]
    
    if available:
        clinician = available[0].clinician
        conversation.assigned_clinician = clinician
        conversation.clinician_assigned_at = timezone.now()
        conversation.save()
        
        PatientAssignment.objects.create(
            patient=conversation.patient,
            clinician=clinician,
            conversation=conversation,
            assignment_reason='AUTO_MATCH'
        )
        
        try:
            handler = ClinicianWhatsAppHandler()
            handler.notify_new_patient(clinician, conversation)
        except:
            pass