import json
import logging
from datetime import datetime
from django.conf import settings
from services.groq_service import GroqService
from services.fallback_service import FallbackService

logger = logging.getLogger('lifegate')


class AIEngine:
    """Main AI engine for triage and assessment generation."""
    
    def __init__(self):
        self.groq = GroqService()
        self.fallback = FallbackService()
    
    def generate_first_question(self, age, gender, chief_complaint):
        """
        Generate first contextual triage question.
        
        Args:
            age: Patient age
            gender: Patient gender
            chief_complaint: Chief complaint
        
        Returns:
            str: First triage question
        """
        system_prompt = """You are a medical triage AI assistant. Generate ONE specific, 
        realistic follow-up question to better understand the patient's symptoms. 
        Keep the question under 150 characters. Be empathetic and professional."""
        
        user_prompt = f"""
        Patient Age: {age}
        Patient Gender: {gender}
        Chief Complaint: {chief_complaint}
        
        Generate ONE clarifying question to understand their symptoms better.
        """
        
        try:
            response = self.groq.call_api(system_prompt, user_prompt, max_tokens=100)
            return response.strip()
        except Exception as e:
            logger.error(f"Error generating first question: {str(e)}")
            return self.fallback.get_first_question(chief_complaint)
    
    def generate_next_question(self, conversation, current_response):
        """
        Generate next contextual triage question.
        
        Args:
            conversation: ConversationSession object
            current_response: Patient's response to last question
        
        Returns:
            str: Next triage question
        """
        # Build conversation history
        qa_pairs = []
        questions = conversation.triage_questions.all().order_by('question_order')
        
        for q in questions:
            if q.patient_response:
                qa_pairs.append({
                    'question': q.question_text,
                    'response': q.patient_response
                })
        
        system_prompt = """You are a medical triage AI. Generate ONE specific, 
        context-aware follow-up question based on the patient's responses. 
        Consider what you've learned so far. Keep under 150 characters."""
        
        user_prompt = f"""
        Patient Age: {conversation.patient.patient_profile.age}
        Patient Gender: {conversation.patient.patient_profile.gender}
        Chief Complaint: {conversation.chief_complaint}
        
        Previous Q&A:
        {json.dumps(qa_pairs, indent=2)}
        
        Last Response: {current_response}
        
        Generate ONE clarifying follow-up question. Be specific and context-aware.
        """
        
        try:
            response = self.groq.call_api(system_prompt, user_prompt, max_tokens=100)
            return response.strip()
        except Exception as e:
            logger.error(f"Error generating next question: {str(e)}")
            return self.fallback.get_next_question()
    
    def generate_assessment(self, conversation):
        """
        Generate structured clinical assessment from triage data.
        
        Args:
            conversation: ConversationSession with all triage data
        
        Returns:
            dict: Structured assessment JSON
        """
        # Gather all Q&A
        qa_data = []
        for q in conversation.triage_questions.all():
            qa_data.append({
                'question': q.question_text,
                'response': q.patient_response
            })
        
        profile = conversation.patient.patient_profile
        
        system_prompt = """You are a medical AI assistant generating a clinical assessment.
        Output ONLY valid JSON with NO markdown, NO explanations, NO code blocks.
        The JSON must be valid and parseable."""
        
        user_prompt = f"""
        Generate a clinical assessment JSON for:
        
        Age: {profile.age}
        Gender: {profile.gender}
        Chief Complaint: {conversation.chief_complaint}
        
        Triage Q&A:
        {json.dumps(qa_data, indent=2)}
        
        Return ONLY this JSON structure:
        {{
            "symptoms_overview": {{
                "primary_symptoms": ["symptom1", "symptom2"],
                "secondary_symptoms": ["symptom"],
                "severity_rating": 1-10,
                "duration": "string",
                "onset": "sudden/gradual",
                "triggers": ["trigger1"]
            }},
            "key_observations": {{
                "likely_condition": "string",
                "risk_factors": ["factor1"],
                "notes": "string"
            }},
            "preliminary_recommendations": {{
                "lifestyle_changes": ["change1", "change2"],
                "monitoring": ["item1"],
                "activities_to_avoid": ["activity1"]
            }},
            "otc_suggestions": {{
                "medications": [
                    {{
                        "name": "med_name",
                        "dosage": "dose_string",
                        "frequency": "freq_string",
                        "notes": "string"
                    }}
                ]
            }},
            "monitoring_advice": {{
                "what_to_monitor": ["item1"],
                "frequency": "daily/weekly",
                "when_to_seek_help": ["condition1"]
            }},
            "red_flags_detected": [],
            "confidence_score": 0.85,
            "notes_for_clinician": "string"
        }}
        """
        
        try:
            response = self.groq.call_api(system_prompt, user_prompt, max_tokens=1500)
            assessment = self._parse_json_response(response)
            
            if not assessment:
                assessment = self.fallback.get_assessment(
                    chief_complaint=conversation.chief_complaint,
                    profile=profile
                )
            
            return assessment
        
        except Exception as e:
            logger.error(f"Error generating assessment: {str(e)}")
            return self.fallback.get_assessment(
                chief_complaint=conversation.chief_complaint,
                profile=profile
            )
    
    def detect_red_flags(self, text):
        """
        Detect emergency red flags in text.
        
        Args:
            text: Patient message text
        
        Returns:
            list: Detected red flags
        """
        text_lower = text.lower()
        red_flags = []
        
        for keyword in settings.RED_FLAG_KEYWORDS:
            if keyword in text_lower:
                red_flags.append(keyword)
        
        return red_flags
    
    def _parse_json_response(self, response):
        """
        Parse JSON from AI response, handling markdown wrappers.
        
        Args:
            response: AI response text
        
        Returns:
            dict: Parsed JSON or None
        """
        try:
            # Remove markdown code blocks if present
            if '```json' in response:
                response = response.split('```json')[1].split('```')[0]
            elif '```' in response:
                response = response.split('```')[1].split('```')[0]
            
            return json.loads(response.strip())
        except (json.JSONDecodeError, IndexError) as e:
            logger.error(f"Error parsing JSON response: {str(e)}")
            return None