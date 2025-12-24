import logging
import json
from django.conf import settings
from twilio.rest import Client as TwilioRestClient
from twilio.request_validator import RequestValidator

logger = logging.getLogger('lifegate')

class TwilioClient:
    """Twilio WhatsApp integration."""
    
    def __init__(self):
        self.account_sid = settings.TWILIO_ACCOUNT_SID
        self.auth_token = settings.TWILIO_AUTH_TOKEN
        
        # Ensure self.whatsapp_number always has exactly one 'whatsapp:' prefix
        raw_number = settings.TWILIO_WHATSAPP_NUMBER
        if raw_number and not raw_number.startswith('whatsapp:'):
            self.whatsapp_number = f"whatsapp:{raw_number}"
        else:
            self.whatsapp_number = raw_number
        
        if self.account_sid and self.auth_token:
            self.client = TwilioRestClient(self.account_sid, self.auth_token)
            self.validator = RequestValidator(self.auth_token)
        else:
            self.client = None
            self.validator = None
    
    def send_message(self, to_whatsapp_id, message_body):
        """Send standard text message."""
        if not self.client:
            logger.error("Twilio client not configured")
            return None
            
        # 1. FIX: Check for None BEFORE doing anything else
        if not to_whatsapp_id:
            logger.error("Attempted to send message to None/Empty number")
            return None

        # 2. FIX: Standardize the prefix
        if not to_whatsapp_id.startswith('whatsapp:'):
            to_whatsapp_id = f"whatsapp:{to_whatsapp_id}"
        
        try:
            # 3. FIX: Removed the undefined 'to_number' logic
            message = self.client.messages.create(
                from_=self.whatsapp_number,
                to=to_whatsapp_id,
                body=message_body
            )
            logger.info(f"Message sent: {message.sid} to {to_whatsapp_id}")
            return message.sid

        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {str(e)}")
            return None

    def send_message_with_buttons(self, to_whatsapp_id, message_body, buttons):
        """
        Send a message with interactive buttons (Quick Replies).
        Args:
            buttons: List of dicts [{'id': 'START', 'title': 'Start'}]
        """
        if not self.client:
            return None
            
        if not to_whatsapp_id:
            return None

        if not to_whatsapp_id.startswith('whatsapp:'):
            to_whatsapp_id = f"whatsapp:{to_whatsapp_id}"

        try:
            # 1. Format buttons for Twilio Content API
            formatted_actions = []
            for btn in buttons:
                formatted_actions.append({
                    'title': btn['title'], 
                    'id': btn['id']
                })

            # 2. Create a dynamic template (Quickest way for Dev)
            # Note: This requires the new Content API setup in Twilio Console
            content = self.client.content.v1.contents.create(
                friendly_name=f'dynamic_{to_whatsapp_id[-4:]}',
                variables={'1': 'placeholder'}, 
                types={
                    'twilio/quick-reply': {
                        'body': message_body,
                        'actions': formatted_actions
                    }
                },
                language='en'
            )
            
            # 3. Send using the Content SID
            message = self.client.messages.create(
                from_=self.whatsapp_number,
                to=to_whatsapp_id,
                content_sid=content.sid,
            )
            logger.info(f"Button message sent: {message.sid}")
            return message.sid

        except Exception as e:
            logger.error(f"Error sending button message: {str(e)}")
            # Fallback to text if buttons fail
            fallback_txt = message_body + "\n\nReply: " + ", ".join([b['title'] for b in buttons])
            return self.send_message(to_whatsapp_id, fallback_txt)

    def send_media_message(self, to_whatsapp_id, media_url, caption=None):
        if not self.client:
            return None
            
        if not to_whatsapp_id:
            return None

        if not to_whatsapp_id.startswith('whatsapp:'):
            to_whatsapp_id = f"whatsapp:{to_whatsapp_id}"
        
        try:
            message = self.client.messages.create(
                from_=self.whatsapp_number,
                to=to_whatsapp_id,
                media_url=media_url,
                body=caption or ""
            )
            logger.info(f"Media message sent: {message.sid}")
            return message.sid
        except Exception as e:
            logger.error(f"Error sending media message: {str(e)}")
            return None
    
    def validate_request(self, request_url, post_params, signature):
        if not self.validator:
            return False
        try:
            return self.validator.validate(request_url, post_params, signature)
        except Exception as e:
            logger.error(f"Error validating signature: {str(e)}")
            return False

    def is_configured(self):
        return bool(self.client and self.account_sid and self.auth_token)