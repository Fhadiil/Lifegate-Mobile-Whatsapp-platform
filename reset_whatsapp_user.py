from apps.authentication.models import User, PatientProfile
from apps.conversations.models import ConversationSession, Message, TriageQuestion
from apps.assessments.models import AIAssessment
from apps.escalations.models import EscalationAlert
from apps.subscriptions.models import PatientSubscription, PaymentHistory


user = User.objects.filter(whatsapp_id__endswith='+2347043851299').first()

if user:
    # Delete related conversations
    conversations = ConversationSession.objects.filter(patient=user)
    
    for convo in conversations:
        TriageQuestion.objects.filter(conversation=convo).delete()
        AIAssessment.objects.filter(conversation=convo).delete()
        Message.objects.filter(conversation=convo).delete()
        EscalationAlert.objects.filter(conversation=convo).delete()
    
    conversations.delete()
    
    # Delete subscriptions/payments
    PatientSubscription.objects.filter(user=user).delete()
    PaymentHistory.objects.filter(user=user).delete()
    
    # Delete patient profile
    if hasattr(user, 'patient_profile'):
        user.patient_profile.delete()
    
    # Delete user itself (optional)
    user.delete()
    
    print("✅ All data cleared for this patient")
else:
    print("❌ User not found")
