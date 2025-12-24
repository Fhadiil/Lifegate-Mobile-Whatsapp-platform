import logging
from django.conf import settings
from groq import Groq

logger = logging.getLogger('lifegate')


class GroqService:
    """Groq API integration for medical AI."""
    
    MODEL = "llama-3.3-70b-versatile"
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS = 500
    
    def __init__(self):
        self.client = Groq(api_key=settings.GROQ_API_KEY)
    
    def call_api(self, system_prompt, user_prompt, max_tokens=None, temperature=None):
        """
        Call Groq API with medical context.
        
        Args:
            system_prompt: System context
            user_prompt: User message
            max_tokens: Maximum tokens (default: 500)
            temperature: Creativity parameter (default: 0.7)
        
        Returns:
            str: AI response
        """
        max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS
        temperature = temperature or self.DEFAULT_TEMPERATURE
        
        try:
            message = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            
            response = message.choices[0].message.content
            logger.debug(f"Groq API response: {response[:100]}...")
            return response
        
        except Exception as e:
            logger.error(f"Groq API error: {str(e)}")
            raise
    
    def generate_triage_question(self, context):
        """
        Generate a medical triage question.
        
        Args:
            context: dict with age, gender, chief_complaint, conversation_history
        
        Returns:
            str: Generated question
        """
        system_prompt = """You are an expert medical triage AI assistant. 
        Your role is to ask ONE specific, clinically relevant follow-up question 
        to better understand the patient's condition. 
        Keep questions under 150 characters. Be empathetic."""
        
        history_text = ""
        if context.get('conversation_history'):
            for item in context['conversation_history'][-3:]:  # Last 3 exchanges
                history_text += f"Q: {item.get('question', '')}\nA: {item.get('response', '')}\n"
        
        user_prompt = f"""
        Patient Profile:
        - Age: {context.get('age')}
        - Gender: {context.get('gender')}
        - Chief Complaint: {context.get('chief_complaint')}
        
        Recent Conversation:
        {history_text}
        
        Generate ONE specific follow-up question. Make it clinically relevant and context-aware.
        """
        
        return self.call_api(system_prompt, user_prompt, max_tokens=100)
    
    def generate_assessment_json(self, triage_data, profile_data):
        """
        Generate structured clinical assessment.
        
        Args:
            triage_data: List of Q&A pairs
            profile_data: Patient profile info
        
        Returns:
            str: JSON assessment
        """
        import json
        
        system_prompt = """You are a medical AI generating clinical assessments.
        Output ONLY valid JSON with NO markdown, NO explanations.
        Ensure the JSON is perfectly formatted and parseable."""
        
        triage_json = json.dumps(triage_data, indent=2)
        
        user_prompt = f"""
        Generate a clinical assessment for:
        
        Age: {profile_data.get('age')}
        Gender: {profile_data.get('gender')}
        Chief Complaint: {profile_data.get('chief_complaint')}
        
        Triage Q&A:
        {triage_json}
        
        Return ONLY valid JSON matching this structure:
        {{
            "symptoms_overview": {{
                "primary_symptoms": ["symptom1", "symptom2"],
                "secondary_symptoms": ["symptom"],
                "severity_rating": 5,
                "duration": "3 days",
                "onset": "gradual",
                "triggers": ["trigger1"]
            }},
            "key_observations": {{
                "likely_condition": "condition name",
                "risk_factors": ["factor1"],
                "notes": "clinical notes"
            }},
            "preliminary_recommendations": {{
                "lifestyle_changes": ["rest", "hydration"],
                "monitoring": ["symptom severity"],
                "activities_to_avoid": ["strenuous activity"]
            }},
            "otc_suggestions": {{
                "medications": [
                    {{
                        "name": "Ibuprofen",
                        "dosage": "200-400mg",
                        "frequency": "Every 6-8 hours",
                        "notes": "Take with food"
                    }}
                ]
            }},
            "monitoring_advice": {{
                "what_to_monitor": ["symptom1"],
                "frequency": "daily",
                "when_to_seek_help": ["if symptom worsens"]
            }},
            "red_flags_detected": [],
            "confidence_score": 0.85,
            "notes_for_clinician": "additional notes"
        }}
        """
        
        return self.call_api(system_prompt, user_prompt, max_tokens=1500)
    
    def detect_red_flags_ai(self, text):
        """
        Use AI to detect medical emergency red flags.
        
        Args:
            text: Patient message
        
        Returns:
            list: Detected red flags with severity
        """
        system_prompt = """You are a medical emergency detection AI.
        Analyze the text for medical red flags indicating emergencies.
        Return ONLY a JSON array of detected flags."""
        
        user_prompt = f"""
        Analyze for red flags:
        "{text}"
        
        Return ONLY a JSON array like: ["flag1", "flag2"]
        """
        
        try:
            response = self.call_api(system_prompt, user_prompt, max_tokens=200)
            import json
            return json.loads(response)
        except:
            return []