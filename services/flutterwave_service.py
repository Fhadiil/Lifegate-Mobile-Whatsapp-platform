import requests
import uuid
from django.conf import settings

class FlutterwaveService:
    BASE_URL = "https://api.flutterwave.com/v3"
    
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
            "Content-Type": "application/json"
        }

    def initialize_payment(self, user, amount, tx_ref):
        """
        Generate a payment link.
        Amount is in Naira (Flutterwave handles the conversion internally usually, but standard is base currency).
        """
        url = f"{self.BASE_URL}/payments"
        
        # Site URL for redirect (ensure this is set in settings)
        base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000')
        
        data = {
            "tx_ref": tx_ref,
            "amount": str(amount), # Flutterwave expects string or number
            "currency": "NGN",
            "redirect_url": f"{base_url}/payment-success/", # Simple success page
            "payment_options": "card,banktransfer,ussd",
            "customer": {
                "email": user.email or f"{user.phone_number}@lifegate.com",
                "phonenumber": user.phone_number,
                "name": f"{user.first_name} {user.last_name}" or "Patient"
            },
            "customizations": {
                "title": "Lifegate Medical",
                "description": "Monthly Access Subscription",
                }
        }
        
        try:
            response = requests.post(url, headers=self.headers, json=data)
            response_data = response.json()
            
            if response_data.get('status') == 'success':
                return response_data['data']['link']
            
            print(f"Flutterwave Init Error: {response_data}")
            return None
        except Exception as e:
            print(f"Flutterwave Connection Error: {e}")
            return None

    def verify_transaction(self, transaction_id):
        """Verify payment status using Transaction ID."""
        url = f"{self.BASE_URL}/transactions/{transaction_id}/verify"
        
        try:
            response = requests.get(url, headers=self.headers)
            data = response.json()
            
            if (data.get('status') == 'success' and 
                data['data']['status'] == 'successful' and
                data['data']['currency'] == 'NGN'):
                return True, data['data']
            return False, None
        except:
            return False, None