import json
import logging
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.http import HttpResponse
from django.views import View
from django.utils import timezone

from .models import PatientSubscription, PaymentHistory
from integrations.twilio.client import TwilioClient
from services.flutterwave_service import FlutterwaveService
from services.workflow_service import finalize_consultation_flow
from apps.assessments.models import AIAssessment

logger = logging.getLogger('lifegate')


def activate_subscription(tx_ref, transaction_id=None):
    """
    Finds transaction, adds credits to user wallet.
    """
    try:
        history = PaymentHistory.objects.get(reference=tx_ref)
        
        if history.status == 'SUCCESS':
            return True, "Already processed", history

        # 1. Mark Payment Success
        history.status = 'SUCCESS'
        history.flutterwave_id = transaction_id
        history.verified_at = timezone.now()
        history.save()
        
        # 2. 👇 ADD CREDITS TO WALLET
        profile = history.user.patient_profile
        credits_to_add = history.package.credits
        
        profile.consultation_credits += credits_to_add
        profile.save()
        
        logger.info(f"Added {credits_to_add} credits to {history.user.phone_number}")
        
        pending_assessment = AIAssessment.objects.filter(
            patient=history.user,
            status='PENDING_PAYMENT'
        ).last() 
        
        if pending_assessment:
            
            conversation = pending_assessment.conversation
            finalize_consultation_flow(history.user, conversation, pending_assessment)
            
            return True, "Resumed Flow", history
        
        return True, "Credits Added", history
        
    except Exception as e:
        logger.error(f"Credit Add Error: {e}")
        return False, str(e), None


def send_confirmation_message(history, status_msg="Credits Added"):
    """Sends the WhatsApp confirmation."""
    try:
        twilio = TwilioClient()
        new_balance = history.user.patient_profile.consultation_credits
        
        if status_msg == "Resumed Flow":
            # Silent or simplified, because finalize_consultation_flow already sent a message
            return
        
        msg_text = (
            f"✅ *PAYMENT SUCCESSFUL*\n\n"
            f"You bought: {history.package.name}\n"
            f"Credits Added: +{history.package.credits}\n"
            f"💰 *Total Balance: {new_balance} Credits*\n\n"
            f"Reply *Hi* to start your consultation (1 credit will be used)."
        )
        twilio.send_message(history.user.whatsapp_id, msg_text)
        logger.info(f"Confirmation sent to {history.user.whatsapp_id}")
    except Exception as e:
        logger.error(f"Twilio Send Error: {e}")



@method_decorator(csrf_exempt, name='dispatch')
class FlutterwaveWebhookView(View):
    def post(self, request, *args, **kwargs):
        # 1. Security Check
        secret_hash = getattr(settings, 'FLUTTERWAVE_SECRET_HASH', '')
        signature = request.headers.get('verif-hash')
        if not signature or signature != secret_hash:
            return HttpResponse(status=401)

        try:
            payload = json.loads(request.body)
            event = payload.get('event')
            data = payload.get('data', {})
            
            if event == 'charge.completed' and data.get('status') == 'successful':
                tx_ref = data.get('tx_ref')
                flw_id = data.get('id')
                
                # Activate
                success, msg, history = activate_subscription(tx_ref, flw_id)
                
                # Notify ONLY if this webhook triggered the activation
                if success:
                    if msg == "Resumed Flow":
                        # Do nothing or log it, the finalize_flow handled the notification
                        pass
                    elif msg == "Credits Added":
                        send_confirmation_message(history)
            
            return HttpResponse(status=200)
        except Exception as e:
            logger.error(f"Webhook error: {str(e)}")
            return HttpResponse(status=400)


class PaymentSuccessView(View):
    def get(self, request, *args, **kwargs):
        status = request.GET.get('status')
        tx_ref = request.GET.get('tx_ref')
        transaction_id = request.GET.get('transaction_id')

        activated = False
        whatsapp_link = "https://wa.me/"

        if (status == 'successful' or status == 'completed') and tx_ref:
            # 1. Activate
            success, msg, history = activate_subscription(tx_ref, transaction_id)
            
            if success:
                activated = True
                if msg == "Resumed Flow":
                    # Do nothing or log it, the finalize_flow handled the notification
                    pass
                elif msg == "Credits Added":
                    send_confirmation_message(history)

        # 4. Render HTML
        status_header = "Payment Received!" if activated else "Processing..."
        status_text = "Your subscription is active. Check WhatsApp for confirmation." if activated else "Verifying payment..."
        
        html = f"""
        <!DOCTYPE html>
        <html>
            <head>
                <title>Payment Successful</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{ font-family: 'Segoe UI', sans-serif; text-align: center; padding: 20px; background: #f0fdf4; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                    .card {{ background: white; padding: 40px; border-radius: 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); max-width: 400px; width: 100%; }}
                    .icon {{ font-size: 60px; margin-bottom: 20px; }}
                    h1 {{ color: #166534; margin: 0 0 10px 0; font-size: 24px; }}
                    p {{ color: #374151; margin-bottom: 30px; line-height: 1.5; }}
                    .btn {{ display: block; background: #25D366; color: white; padding: 15px; text-decoration: none; border-radius: 12px; font-weight: bold; transition: background 0.3s; }}
                    .btn:hover {{ background: #128C7E; }}
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="icon">✅</div>
                    <h1>{status_header}</h1>
                    <p>{status_text}</p>
                    <a href="https://wa.me/+14155238886" class="btn">Return to WhatsApp</a>
                </div>
            </body>
        </html>
        """
        return HttpResponse(html)