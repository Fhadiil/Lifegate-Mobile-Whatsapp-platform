# services/ai_engine.py
import logging
import json
from services.groq_service import GroqService

logger = logging.getLogger('lifegate')


class AIEngine:
    """AI Engine for medical triage and assessment generation."""
    
    def __init__(self):
        self.groq = GroqService()
    
    def generate_first_question(self, age, gender, chief_complaint):
        """
        Generate the first triage question based on chief complaint.
        
        Args:
            age: Patient age
            gender: Patient gender
            chief_complaint: Patient's main symptom/concern
        
        Returns:
            str: First triage question
        """
        
        system_prompt = """You are a medical triage AI assistant. Generate a focused follow-up question to understand the patient's symptoms better.

Rules:
- Ask ONE specific question
- Focus on symptom details (onset, duration, severity, location)
- Keep it conversational and empathetic
- No more than 20 words
- Don't diagnose"""

        user_prompt = f"""Patient: {gender}, Age {age}
Chief Complaint: {chief_complaint}

Generate the first follow-up question to understand their symptoms better."""

        try:
            response = self.groq.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=100
            )
            
            question = response.choices[0].message.content.strip()
            return question
            
        except Exception as e:
            logger.error(f"Error generating first question: {e}")
            return "Can you tell me more about when this started and how severe it is?"
    
    def generate_next_question(self, conversation, current_response):
        """
        Generate next triage question based on conversation history.
        
        Args:
            conversation: ConversationSession object
            current_response: Patient's latest response
        
        Returns:
            str: Next triage question
        """
        
        # Get all previous Q&A pairs
        triage_history = conversation.triage_questions.filter(
            response_processed=True
        ).order_by('question_order')
        
        history_text = f"Chief Complaint: {conversation.chief_complaint}\n\n"
        
        for triage in triage_history:
            history_text += f"Q: {triage.question_text}\n"
            history_text += f"A: {triage.patient_response}\n\n"
        
        system_prompt = """You are a medical triage AI. Generate the next logical question to complete the assessment.

Focus on:
- Associated symptoms
- Risk factors
- Previous medical history
- Medication use
- Red flags

Keep questions conversational, empathetic, and under 20 words."""

        user_prompt = f"""Triage History:
{history_text}

Latest Answer: {current_response}

Generate the next important question to complete the assessment."""

        try:
            response = self.groq.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=100
            )
            
            question = response.choices[0].message.content.strip()
            return question
            
        except Exception as e:
            logger.error(f"Error generating next question: {e}")
            return "Is there anything else about your symptoms you'd like to share?"
    
    def generate_assessment(self, conversation):
        """
        Generate comprehensive medical assessment from triage data.
        
        Args:
            conversation: ConversationSession object
        
        Returns:
            dict: Structured assessment data
        """
        
        # Compile triage data
        triage_data = f"Patient: {conversation.patient.patient_profile.gender}, Age {conversation.patient.patient_profile.age}\n"
        triage_data += f"Chief Complaint: {conversation.chief_complaint}\n\n"
        triage_data += "Triage Questions & Answers:\n"
        
        for triage in conversation.triage_questions.filter(response_processed=True).order_by('question_order'):
            triage_data += f"Q: {triage.question_text}\n"
            triage_data += f"A: {triage.patient_response}\n\n"
        
        system_prompt = """You are a medical AI assistant generating structured clinical assessments for physician review.

Generate a comprehensive JSON assessment with this EXACT structure:
{
    "symptoms_overview": {
        "primary_symptoms": ["symptom1", "symptom2"],
        "severity_rating": 5,
        "duration": "3 days"
    },
    "key_observations": {
        "likely_condition": "Condition name",
        "differential_diagnoses": ["option1", "option2"],
        "notes": "Clinical observations"
    },
    "preliminary_recommendations": {
        "lifestyle_changes": ["recommendation1", "recommendation2"]
    },
    "otc_suggestions": {
        "medications": [
            {"name": "Medicine", "dosage": "500mg", "frequency": "twice daily"}
        ]
    },
    "monitoring_advice": {
        "what_to_monitor": ["sign1", "sign2"],
        "when_to_seek_help": ["warning1", "warning2"]
    },
    "red_flags_detected": [],
    "confidence_score": 0.8
}

CRITICAL: Return ONLY valid JSON. No markdown, no explanations."""

        try:
            response = self.groq.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": triage_data}
                ],
                temperature=0.3,
                max_tokens=1500
            )
            
            raw_output = response.choices[0].message.content.strip()
            
            # Clean response (remove markdown if present)
            if raw_output.startswith('```'):
                raw_output = raw_output.split('```')[1]
                if raw_output.startswith('json'):
                    raw_output = raw_output[4:]
            
            assessment_data = json.loads(raw_output)
            
            return assessment_data
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error: {e}")
            logger.error(f"Raw output: {raw_output}")
            
            # Return safe fallback
            return {
                "symptoms_overview": {
                    "primary_symptoms": [conversation.chief_complaint],
                    "severity_rating": 5,
                    "duration": "Unknown"
                },
                "key_observations": {
                    "likely_condition": "Assessment pending",
                    "differential_diagnoses": [],
                    "notes": "Unable to generate full assessment"
                },
                "preliminary_recommendations": {
                    "lifestyle_changes": ["Rest and hydration"]
                },
                "otc_suggestions": {
                    "medications": []
                },
                "monitoring_advice": {
                    "what_to_monitor": ["Symptom progression"],
                    "when_to_seek_help": ["If symptoms worsen"]
                },
                "red_flags_detected": [],
                "confidence_score": 0.5
            }
            
        except Exception as e:
            logger.error(f"Error generating assessment: {e}")
            raise
    
    # ==================== NEW METHOD FOR AI-ONLY MODE ====================
    
    def generate_ai_only_response(self, conversation, user_message):
        """
        Generate educational, non-diagnostic response for AI-only mode.
        
        CRITICAL SAFETY RULES:
        - Never diagnose conditions
        - Never prescribe medications
        - Never give specific medical advice
        - Only provide general health education
        
        Args:
            conversation: ConversationSession object
            user_message: User's current message
        
        Returns:
            str: Safe, educational AI response
        """
        
        # Get conversation history (last 10 messages)
        messages = conversation.messages.filter(
            message_type__in=['PATIENT', 'AI_RESPONSE']
        ).order_by('created_at')[:10]
        
        history = []
        for msg in messages:
            role = 'user' if msg.message_type == 'PATIENT' else 'assistant'
            history.append({"role": role, "content": msg.content})
        
        # Add current message
        history.append({"role": "user", "content": user_message})
        
        # CRITICAL SYSTEM PROMPT - Enforces safety boundaries
        system_prompt = """You are a health education AI assistant. You are NOT a doctor and cannot diagnose or prescribe.

STRICT RULES YOU MUST FOLLOW:
1. NEVER diagnose conditions (don't say "You have...", "This is...", "It sounds like...")
2. NEVER prescribe medications or treatments
3. NEVER give specific medical advice ("You should...", "Take...", "Do...")
4. ONLY answer health and medical questions - politely decline non-medical topics
5. If asked for diagnosis/prescription/specific advice, respond: "I can't provide that - you'd need a licensed clinician for diagnosis or treatment recommendations."

WHAT YOU CAN DO:
✅ Explain medical terms in simple language
✅ Provide general wellness information  
✅ Discuss symptoms in educational context (e.g., "Colds typically cause...")
✅ Suggest when to see a doctor based on severity
✅ Share general health tips

WHAT YOU CANNOT DO:
❌ Answer questions about celebrities, entertainment, sports, politics, etc.
❌ Diagnose or prescribe
❌ Give specific treatment advice

IF ASKED NON-MEDICAL QUESTIONS:
Respond: "I'm a health assistant and can only help with medical and health-related questions. Is there anything about your health I can help with?"

TONE & STYLE:
- Conversational, warm, and empathetic
- Keep responses under 150 words
- Use simple language
- Be helpful without overstepping boundaries

EXAMPLES:
❌ BAD: "You have a sinus infection. Take amoxicillin 500mg."
✅ GOOD: "Those symptoms could be related to several things. A doctor can properly assess and recommend treatment if needed."

❌ BAD: "This sounds like COVID. You should isolate."
✅ GOOD: "If you're concerned about respiratory symptoms, it's best to consult a healthcare provider who can test and advise."

❌ BAD: "Demi Lovato is a singer..."
✅ GOOD: "I'm a health assistant and can only help with medical questions. Is there anything about your health I can help with?"

Remember: Your role is to educate and guide on HEALTH topics only, NOT to diagnose or treat."""

        try:
            response = self.groq.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    *history
                ],
                temperature=0.7,
                max_tokens=300
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # SAFETY CHECK: Scan for prohibited diagnostic language
            diagnostic_patterns = [
                'you have', 'you might have', 'this is', 'sounds like you have',
                'diagnosis', 'you should take', 'i recommend taking', 'take this medication',
                'you need to take', 'prescribed', 'prescription'
            ]
            
            response_lower = ai_response.lower()
            
            # If AI violated rules, override with safe response
            if any(pattern in response_lower for pattern in diagnostic_patterns):
                logger.warning(f"AI-only response violated safety rules. Overriding.")
                ai_response = (
                    "I can't provide diagnosis or specific medical advice - "
                    "that requires a licensed clinician. If you'd like professional "
                    "assessment, I can connect you with a doctor. Otherwise, I'm happy "
                    "to discuss general health information. What would you like to know?"
                )
            
            return ai_response
            
        except Exception as e:
            logger.error(f"AI-only response error: {e}")
            # Safe fallback response
            return "I'm having trouble right now. Could you try rephrasing your question?"
