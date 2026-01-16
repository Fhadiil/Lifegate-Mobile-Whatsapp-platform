from django.db import models
from django.utils import timezone
from datetime import timedelta
from apps.authentication.models import User

class CreditPackage(models.Model):
    name = models.CharField(max_length=50)
    price = models.DecimalField(max_digits=10, decimal_places=2) 
    credits = models.IntegerField(default=1)    
    description = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.name} - {self.credits} Sessions (₦{self.price:,.0f})"

class PaymentHistory(models.Model):
    """Tracks every payment attempt and success."""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    package = models.ForeignKey(CreditPackage, on_delete=models.SET_NULL, null=True)
    reference = models.CharField(max_length=100, unique=True) # The tx_ref
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    flutterwave_id = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.phone_number} - {self.amount} ({self.status})"

class PatientSubscription(models.Model):
    """Tracks ONLY the current active status."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    # We don't need reference here anymore, we look it up in PaymentHistory
    start_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField()
    is_active = models.BooleanField(default=False)

    def is_valid(self):
        if not self.is_active: return False
        return timezone.now() < self.end_date

    def activate(self, days):
        self.is_active = True
        self.start_date = timezone.now()
        self.end_date = timezone.now() + timedelta(days=days)
        self.save()

    def __str__(self):
        return f"{self.user.phone_number} - {'Active' if self.is_valid() else 'Expired'}"