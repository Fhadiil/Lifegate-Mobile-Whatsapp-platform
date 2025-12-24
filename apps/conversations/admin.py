from django.contrib import admin
from .models import ConversationSession, Message, TriageQuestion

admin.site.register(ConversationSession)
admin.site.register(Message)
admin.site.register(TriageQuestion)