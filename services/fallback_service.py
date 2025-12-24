"""Fallback responses when AI service is unavailable."""


class FallbackService:
    """Provide fallback responses for when Groq API fails."""
    
    FIRST_QUESTIONS = {
        'default': "When did your symptoms first start?",
        'headache': "Is the headache throbbing, pressure-like, or sharp?",
        'fever': "What's your current body temperature?",
        'cough': "Is your cough dry or productive (with mucus)?",
        'pain': "On a scale of 1-10, how severe is your pain?",
        'nausea': "Have you vomited, or just feeling nauseous?",
    }
    
    NEXT_QUESTIONS = [
        "Have you had this symptom before?",
        "Are you taking any medications?",
        "Do you have any known medical conditions?",
        "Have you tried any home remedies?",
        "Are any other family members experiencing similar symptoms?",
        "Is there anything that makes it better or worse?",
        "Have you experienced any fever?",
    ]
    
    def get_first_question(self, chief_complaint):
        """Get first fallback question based on chief complaint."""
        complaint_lower = chief_complaint.lower()
        
        for keyword, question in self.FIRST_QUESTIONS.items():
            if keyword != 'default' and keyword in complaint_lower:
                return question
        
        return self.FIRST_QUESTIONS['default']
    
    def get_next_question(self, index=0):
        """Get next fallback question."""
        return self.NEXT_QUESTIONS[index % len(self.NEXT_QUESTIONS)]
    
    def get_assessment(self, chief_complaint, profile):
        """Get fallback assessment."""
        return {
            "symptoms_overview": {
                "primary_symptoms": [chief_complaint],
                "secondary_symptoms": [],
                "severity_rating": 5,
                "duration": "unknown",
                "onset": "gradual",
                "triggers": []
            },
            "key_observations": {
                "likely_condition": "Assessment pending clinician review",
                "risk_factors": [],
                "notes": "Please wait for clinician review for diagnosis"
            },
            "preliminary_recommendations": {
                "lifestyle_changes": [
                    "Get adequate rest",
                    "Stay hydrated",
                    "Monitor your symptoms"
                ],
                "monitoring": ["Symptom progression"],
                "activities_to_avoid": []
            },
            "otc_suggestions": {
                "medications": [
                    {
                        "name": "Over-the-counter pain relief",
                        "dosage": "As directed on package",
                        "frequency": "As needed",
                        "notes": "Consult clinician before use"
                    }
                ]
            },
            "monitoring_advice": {
                "what_to_monitor": ["Symptom severity", "Any new symptoms"],
                "frequency": "daily",
                "when_to_seek_help": [
                    "If symptoms worsen",
                    "If new symptoms develop",
                    "As recommended by clinician"
                ]
            },
            "red_flags_detected": [],
            "confidence_score": 0.5,
            "notes_for_clinician": "Generated from fallback service - Please review thoroughly"
        }