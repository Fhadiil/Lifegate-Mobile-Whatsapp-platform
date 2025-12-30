import logging
import json
from typing import Dict, List, Tuple
from django.conf import settings

logger = logging.getLogger('lifegate')


class AssessmentModificationValidator:
    """
    Validate clinician modifications using AI before sending to patient.
    Checks for:
    - Medical safety (dangerous dosages, interactions)
    - Consistency (recommendations match condition)
    - Completeness (all critical info present)
    - Quality (grammar, clarity)
    """
    
    def __init__(self):
        self.severity_levels = {
            'CRITICAL': 'Must stop - patient safety at risk',
            'HIGH': 'Warning - should reconsider',
            'MEDIUM': 'Caution - review recommended',
            'LOW': 'Info - minor issue detected'
        }
    
    def validate_modification(self, assessment, modification_session) -> Dict:
        """
        Comprehensive validation of assessment modification.
        
        Returns:
            {
                'is_valid': bool,
                'severity': 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'OK',
                'issues': [
                    {
                        'type': 'medication_safety' | 'consistency' | 'completeness' | 'quality',
                        'severity': 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW',
                        'message': 'Human readable message',
                        'suggestion': 'How to fix it'
                    }
                ],
                'warnings': [list of warning messages],
                'summary': 'Human readable summary',
                'recommendation': 'SEND' | 'REVIEW' | 'DO_NOT_SEND'
            }
        """
        
        issues = []
        
        logger.info(f"[VALIDATOR] Starting validation for assessment {assessment.id}")
        
        # Check 1: Medications validation
        med_issues = self._validate_medications(
            assessment,
            modification_session.modified_otc_suggestions
        )
        issues.extend(med_issues)
        
        # Check 2: Recommendations consistency
        rec_issues = self._validate_recommendations(
            assessment,
            modification_session.modified_recommendations
        )
        issues.extend(rec_issues)
        
        # Check 3: Monitoring/When to seek help
        mon_issues = self._validate_monitoring(
            assessment,
            modification_session.modified_monitoring_advice
        )
        issues.extend(mon_issues)
        
        # Check 4: Doctor's note quality
        note_issues = self._validate_notes(modification_session.clinician_notes)
        issues.extend(note_issues)
        
        # Check 5: Overall consistency
        consistency_issues = self._validate_overall_consistency(
            assessment,
            modification_session
        )
        issues.extend(consistency_issues)
        
        # Determine severity and recommendation
        severity = self._determine_severity(issues)
        warnings = [issue['message'] for issue in issues]
        recommendation = self._get_recommendation(severity, issues)
        summary = self._build_summary(assessment, modification_session, issues)
        
        result = {
            'is_valid': len([i for i in issues if i['severity'] in ['CRITICAL', 'HIGH']]) == 0,
            'severity': severity,
            'issues': issues,
            'warnings': warnings,
            'summary': summary,
            'recommendation': recommendation,
            'validated_at': str(__import__('django.utils.timezone', fromlist=['now']).now())
        }
        
        logger.info(f"[VALIDATOR] Validation complete - Severity: {severity}, Issues: {len(issues)}")
        
        return result
    
    # ============= MEDICATION VALIDATION =============
    
    def _validate_medications(self, assessment, modified_meds) -> List[Dict]:
        """Validate medications for safety and appropriateness."""
        
        issues = []
        
        if not modified_meds:
            return issues
        
        medications = modified_meds.get('medications', [])
        
        if not medications:
            return issues
        
        original_meds = assessment.otc_suggestions.get('medications', []) if assessment.otc_suggestions else []
        condition = assessment.key_observations.get('likely_condition', '') if assessment.key_observations else ''
        
        for med in medications:
            if isinstance(med, dict):
                med_name = med.get('name', '').lower()
                dosage = med.get('dosage', '').lower()
                frequency = med.get('frequency', '').lower()
                
                # Check 1: Dosage validation
                if dosage:
                    dosage_issues = self._check_dosage_safety(med_name, dosage)
                    issues.extend(dosage_issues)
                
                # Check 2: Frequency validation
                if frequency:
                    freq_issues = self._check_frequency_safety(med_name, frequency)
                    issues.extend(freq_issues)
                
                # Check 3: Drug interaction check
                drug_issues = self._check_drug_interactions(med_name, medications)
                issues.extend(drug_issues)
                
                # Check 4: Appropriateness for condition
                if condition:
                    appropriateness_issues = self._check_med_appropriateness(med_name, condition)
                    issues.extend(appropriateness_issues)
                
                # Check 5: Compare with original
                original_med_names = [m.get('name', '').lower() if isinstance(m, dict) else str(m).lower() 
                                     for m in original_meds]
                
                if med_name not in original_med_names and med_name not in self._get_safe_otc_drugs():
                    issues.append({
                        'type': 'medication_safety',
                        'severity': 'MEDIUM',
                        'message': f"'{med_name}' is a new medication not in original assessment",
                        'suggestion': f"Verify '{med_name}' is appropriate for {condition or 'patient condition'}"
                    })
        
        return issues
    
    def _check_dosage_safety(self, medication_name: str, dosage: str) -> List[Dict]:
        """Check if dosage is within safe limits."""
        
        issues = []
        med_name = medication_name.lower().strip()
        dosage_lower = dosage.lower()
        
        # Common OTC medications dosage limits
        safe_dosages = {
            'aspirin': ['100mg', '300mg', '500mg', '1000mg', '1g'],
            'ibuprofen': ['200mg', '400mg', '600mg', '800mg'],
            'paracetamol': ['250mg', '500mg', '650mg', '1000mg', '1g'],
            'acetaminophen': ['250mg', '500mg', '650mg', '1000mg', '1g'],
            'diphenhydramine': ['25mg', '50mg'],
            'cetirizine': ['5mg', '10mg'],
            'loratadine': ['5mg', '10mg'],
        }
        
        if med_name in safe_dosages:
            safe_doses = safe_dosages[med_name]
            # Extract numeric part
            try:
                dosage_amount = ''.join(c for c in dosage_lower.split()[0] if c.isdigit() or c == '.')
                if dosage_amount:
                    # Check if it's in safe range
                    is_safe = any(dose.replace('mg', '').replace('g', '') in dosage_lower 
                                 for dose in safe_doses)
                    
                    if not is_safe and dosage_amount:
                        try:
                            dose_num = float(dosage_amount)
                            # Extract max safe dose
                            max_safe = max([float(d.replace('mg', '').replace('g', '')) 
                                          for d in safe_doses])
                            
                            if dose_num > max_safe * 1.5:  # 50% more than max is red flag
                                issues.append({
                                    'type': 'medication_safety',
                                    'severity': 'HIGH',
                                    'message': f"{med_name} dosage of {dosage} may be too high",
                                    'suggestion': f"Recommended: {safe_doses[-1]}, frequency: 2-3 times daily"
                                })
                        except:
                            pass
            except:
                pass
        
        return issues
    
    def _check_frequency_safety(self, medication_name: str, frequency: str) -> List[Dict]:
        """Check if medication frequency is safe."""
        
        issues = []
        med_name = medication_name.lower().strip()
        freq_lower = frequency.lower()
        
        # Safe frequencies for common OTC drugs
        safe_frequencies = {
            'aspirin': ['2-3 times daily', 'three times daily', 'thrice daily'],
            'ibuprofen': ['2-3 times daily', 'three times daily', '3-4 times daily'],
            'paracetamol': ['3-4 times daily', 'every 4-6 hours'],
            'acetaminophen': ['3-4 times daily', 'every 4-6 hours'],
        }
        
        if med_name in safe_frequencies:
            safe_freqs = safe_frequencies[med_name]
            
            # Check for dangerous frequencies
            dangerous_patterns = ['every hour', 'hourly', '10 times', '12 times', 'every 2 hours']
            
            if any(pattern in freq_lower for pattern in dangerous_patterns):
                issues.append({
                    'type': 'medication_safety',
                    'severity': 'CRITICAL',
                    'message': f"Frequency '{frequency}' for {med_name} is dangerously high",
                    'suggestion': f"Use: {safe_freqs[0]}"
                })
        
        return issues
    
    def _check_drug_interactions(self, medication_name: str, all_medications: List) -> List[Dict]:
        """Check for known drug interactions."""
        
        issues = []
        med_name = medication_name.lower().strip()
        
        # Known dangerous interactions
        dangerous_interactions = {
            'aspirin': ['ibuprofen', 'warfarin'],
            'ibuprofen': ['aspirin', 'naproxen'],
            'paracetamol': ['alcohol'],  # If noted in assessment
            'acetaminophen': ['alcohol'],
        }
        
        if med_name in dangerous_interactions:
            other_meds = [m.get('name', '').lower() if isinstance(m, dict) else str(m).lower() 
                         for m in all_medications if isinstance(m, dict) and m.get('name', '').lower() != med_name]
            
            for dangerous_drug in dangerous_interactions[med_name]:
                if any(dangerous_drug in om for om in other_meds):
                    issues.append({
                        'type': 'medication_safety',
                        'severity': 'HIGH',
                        'message': f"Potential interaction: {med_name} + {dangerous_drug}",
                        'suggestion': "Remove one of the conflicting medications or space them out"
                    })
        
        return issues
    
    def _check_med_appropriateness(self, medication_name: str, condition: str) -> List[Dict]:
        """Check if medication is appropriate for the condition."""
        
        issues = []
        med_name = medication_name.lower().strip()
        condition_lower = condition.lower()
        
        # Map conditions to appropriate medications
        condition_med_map = {
            'headache': ['aspirin', 'ibuprofen', 'paracetamol', 'acetaminophen'],
            'fever': ['aspirin', 'ibuprofen', 'paracetamol', 'acetaminophen'],
            'pain': ['aspirin', 'ibuprofen', 'paracetamol', 'acetaminophen'],
            'cold': ['paracetamol', 'ibuprofen', 'decongestant'],
            'allergy': ['cetirizine', 'loratadine', 'antihistamine'],
            'cough': ['cough syrup', 'dextromethorphan'],
        }
        
        # Find matching condition
        appropriate_meds = []
        for cond, meds in condition_med_map.items():
            if cond in condition_lower:
                appropriate_meds.extend(meds)
        
        if appropriate_meds:
            if not any(med in med_name for med in appropriate_meds):
                issues.append({
                    'type': 'consistency',
                    'severity': 'MEDIUM',
                    'message': f"'{med_name}' may not be ideal for {condition}",
                    'suggestion': f"Consider: {', '.join(appropriate_meds[:2])}"
                })
        
        return issues
    
    # ============= RECOMMENDATIONS VALIDATION =============
    
    def _validate_recommendations(self, assessment, modified_recs) -> List[Dict]:
        """Validate lifestyle recommendations."""
        
        issues = []
        
        if not modified_recs:
            return issues
        
        recommendations = modified_recs.get('lifestyle_changes', [])
        
        if not recommendations or len(recommendations) == 0:
            condition = assessment.key_observations.get('likely_condition', '') if assessment.key_observations else ''
            issues.append({
                'type': 'completeness',
                'severity': 'MEDIUM',
                'message': "No lifestyle recommendations provided",
                'suggestion': f"Add at least 2-3 recommendations for {condition or 'patient recovery'}"
            })
        
        # Check for quality of recommendations
        for idx, rec in enumerate(recommendations, 1):
            if isinstance(rec, str):
                rec_lower = rec.lower()
                
                # Check 1: Vague recommendations
                vague_words = ['do something', 'try to', 'maybe', 'possibly', 'perhaps']
                if any(vague in rec_lower for vague in vague_words):
                    issues.append({
                        'type': 'quality',
                        'severity': 'LOW',
                        'message': f"Recommendation #{idx} is vague: '{rec}'",
                        'suggestion': "Be specific with actions. E.g., 'Take 2-3L water daily' instead of 'Stay hydrated'"
                    })
                
                # Check 2: Contradictory recommendations
                conflicting_pairs = [
                    ('stay in bed', 'exercise'),
                    ('avoid all food', 'eat well'),
                    ('rest', 'strenuous activity'),
                ]
                
                for conflict1, conflict2 in conflicting_pairs:
                    if conflict1 in rec_lower:
                        if any(conflict2 in r.lower() for r in recommendations if isinstance(r, str)):
                            issues.append({
                                'type': 'consistency',
                                'severity': 'HIGH',
                                'message': f"Contradictory recommendations: '{rec}' conflicts with other advice",
                                'suggestion': "Remove or clarify conflicting advice"
                            })
        
        return issues
    
    # ============= MONITORING VALIDATION =============
    
    def _validate_monitoring(self, assessment, modified_monitoring) -> List[Dict]:
        """Validate when to seek help guidelines."""
        
        issues = []
        
        if not modified_monitoring:
            return issues
        
        when_to_seek = modified_monitoring.get('when_to_seek_help', [])
        
        if not when_to_seek or len(when_to_seek) == 0:
            issues.append({
                'type': 'completeness',
                'severity': 'HIGH',
                'message': "No emergency warning signs provided",
                'suggestion': "Add at least 2-3 critical warning signs to seek immediate help"
            })
        
        # Check for appropriate warning signs
        for idx, warning in enumerate(when_to_seek, 1):
            if isinstance(warning, str):
                warning_lower = warning.lower()
                
                # Check 1: Specific and measurable
                if len(warning) < 10:
                    issues.append({
                        'type': 'quality',
                        'severity': 'MEDIUM',
                        'message': f"Warning #{idx} is too brief: '{warning}'",
                        'suggestion': "Be specific: 'Fever above 39°C lasting 3+ days' instead of 'High fever'"
                    })
                
                # Check 2: Check for critical symptoms
                critical_keywords = ['severe', 'difficulty breathing', 'chest pain', 'unconscious', 'emergency']
                has_critical = any(keyword in warning_lower for keyword in critical_keywords)
                
                if not has_critical and idx == 1:
                    issues.append({
                        'type': 'completeness',
                        'severity': 'MEDIUM',
                        'message': "First warning sign should be a critical symptom",
                        'suggestion': "Start with most urgent warning: e.g., 'Difficulty breathing or chest pain'"
                    })
        
        return issues
    
    # ============= NOTES VALIDATION =============
    
    def _validate_notes(self, notes: str) -> List[Dict]:
        """Validate doctor's note quality."""
        
        issues = []
        
        if not notes or len(notes.strip()) == 0:
            return issues  # Notes are optional
        
        # Check for professional tone
        unprofessional_words = ['weird', 'lol', 'dunno', 'gonna', 'wanna', 'aint']
        
        if any(word in notes.lower() for word in unprofessional_words):
            issues.append({
                'type': 'quality',
                'severity': 'LOW',
                'message': "Note contains informal language",
                'suggestion': "Use professional medical language. E.g., 'not recommended' instead of 'ain't'"
            })
        
        # Check for excessive length
        if len(notes) > 500:
            issues.append({
                'type': 'quality',
                'severity': 'LOW',
                'message': f"Note is quite long ({len(notes)} chars), may overwhelm patient",
                'suggestion': "Keep notes brief and actionable. Aim for 100-300 characters"
            })
        
        return issues
    
    # ============= OVERALL CONSISTENCY =============
    
    def _validate_overall_consistency(self, assessment, modification_session) -> List[Dict]:
        """Check overall consistency of the modification."""
        
        issues = []
        
        meds = modification_session.modified_otc_suggestions.get('medications', []) if modification_session.modified_otc_suggestions else []
        recs = modification_session.modified_recommendations.get('lifestyle_changes', []) if modification_session.modified_recommendations else []
        warnings = modification_session.modified_monitoring_advice.get('when_to_seek_help', []) if modification_session.modified_monitoring_advice else []
        
        # Check 1: All three sections are filled
        sections_filled = [bool(meds), bool(recs), bool(warnings)]
        
        if len([s for s in sections_filled if s]) < 2:
            issues.append({
                'type': 'completeness',
                'severity': 'MEDIUM',
                'message': "Some sections are empty (medications, recommendations, or warnings)",
                'suggestion': "Ensure all sections have appropriate content"
            })
        
        # Check 2: Verify modifications are actually different from original
        original_meds = assessment.otc_suggestions.get('medications', []) if assessment.otc_suggestions else []
        if len(meds) == len(original_meds) and all(m in original_meds for m in meds if isinstance(m, dict)):
            # Check recommendations
            original_recs = assessment.preliminary_recommendations.get('lifestyle_changes', []) if assessment.preliminary_recommendations else []
            if len(recs) == len(original_recs) and all(r in original_recs for r in recs):
                issues.append({
                    'type': 'quality',
                    'severity': 'LOW',
                    'message': "No modifications detected - assessment is identical to original",
                    'suggestion': "Either modify content or proceed without changes"
                })
        
        return issues
    
    # ============= HELPER METHODS =============
    
    def _get_safe_otc_drugs(self) -> List[str]:
        """Get list of safe OTC drugs."""
        return [
            'aspirin', 'ibuprofen', 'paracetamol', 'acetaminophen',
            'cetirizine', 'loratadine', 'antihistamine', 'decongestant',
            'cough syrup', 'dextromethorphan', 'diphenhydramine',
            'antacid', 'omeprazole', 'ranitidine'
        ]
    
    def _determine_severity(self, issues: List[Dict]) -> str:
        """Determine overall severity from issues."""
        
        if not issues:
            return 'OK'
        
        severities = [issue['severity'] for issue in issues]
        
        if 'CRITICAL' in severities:
            return 'CRITICAL'
        elif 'HIGH' in severities:
            return 'HIGH'
        elif 'MEDIUM' in severities:
            return 'MEDIUM'
        elif 'LOW' in severities:
            return 'LOW'
        
        return 'OK'
    
    def _get_recommendation(self, severity: str, issues: List[Dict]) -> str:
        """Get recommendation based on severity."""
        
        if severity == 'CRITICAL':
            return 'DO_NOT_SEND'
        elif severity == 'HIGH':
            return 'REVIEW'
        elif severity == 'MEDIUM':
            return 'REVIEW'
        else:
            return 'SEND'
    
    def _build_summary(self, assessment, modification_session, issues: List[Dict]) -> str:
        """Build human-readable summary."""
        
        summary = "📋 *VALIDATION REPORT*\n\n"
        
        if not issues:
            summary += "✅ *All checks passed!*\n"
            summary += "Safe to send to patient.\n"
        else:
            critical = [i for i in issues if i['severity'] == 'CRITICAL']
            high = [i for i in issues if i['severity'] == 'HIGH']
            medium = [i for i in issues if i['severity'] == 'MEDIUM']
            low = [i for i in issues if i['severity'] == 'LOW']
            
            if critical:
                summary += f"🛑 *CRITICAL ISSUES* ({len(critical)})\n"
                for issue in critical[:2]:
                    summary += f"  • {issue['message']}\n"
                if len(critical) > 2:
                    summary += f"  ... +{len(critical)-2} more\n"
                summary += "\n"
            
            if high:
                summary += f"⚠️ *HIGH PRIORITY* ({len(high)})\n"
                for issue in high[:2]:
                    summary += f"  • {issue['message']}\n"
                if len(high) > 2:
                    summary += f"  ... +{len(high)-2} more\n"
                summary += "\n"
            
            if medium:
                summary += f"🔶 *WARNINGS* ({len(medium)})\n"
                for issue in medium[:2]:
                    summary += f"  • {issue['message']}\n"
                if len(medium) > 2:
                    summary += f"  ... +{len(medium)-2} more\n"
                summary += "\n"
            
            if low:
                summary += f"ℹ️ *NOTES* ({len(low)})\n"
                for issue in low[:1]:
                    summary += f"  • {issue['message']}\n"
                if len(low) > 1:
                    summary += f"  ... +{len(low)-1} more\n"
        
        return summary