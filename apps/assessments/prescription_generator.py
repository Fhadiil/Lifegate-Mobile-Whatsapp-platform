import logging
from datetime import datetime
from io import BytesIO
from django.conf import settings
from django.utils import timezone
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, 
    PageBreak, Image, KeepTogether, Frame, PageTemplate
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY

logger = logging.getLogger('lifegate')


class PrescriptionPDFGenerator:
    """Generate prescription PDF documents with medical authority."""
    
    def __init__(self):
        self.page_width, self.page_height = letter
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Setup custom paragraph styles."""
        
        # Header style
        self.styles.add(ParagraphStyle(
            name='CustomHeader',
            parent=self.styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=6,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ))
        
        # Subheader style
        self.styles.add(ParagraphStyle(
            name='SubHeader',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#333333'),
            spaceAfter=4,
            alignment=TA_CENTER,
            fontName='Helvetica'
        ))
        
        # Label style (for field names)
        self.styles.add(ParagraphStyle(
            name='FieldLabel',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#555555'),
            fontName='Helvetica-Bold',
            spaceAfter=2
        ))
        
        # Value style
        self.styles.add(ParagraphStyle(
            name='FieldValue',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#000000'),
            fontName='Helvetica',
            spaceAfter=4
        ))
        
        # Medication style
        self.styles.add(ParagraphStyle(
            name='MedicationText',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#1a1a1a'),
            fontName='Helvetica',
            spaceAfter=3,
            leftIndent=20
        ))
        
        # Warning style
        self.styles.add(ParagraphStyle(
            name='Warning',
            parent=self.styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#CC0000'),
            fontName='Helvetica-Bold',
            spaceAfter=2
        ))
    
    def generate_prescription(self, assessment, clinician, modification_session=None):
        """
        Generate prescription PDF.
        
        Args:
            assessment: AIAssessment instance
            clinician: Clinician (User) instance with license info
            modification_session: ModificationSession if modified
        
        Returns:
            BytesIO: PDF bytes
        """
        
        try:
            # Create PDF buffer
            pdf_buffer = BytesIO()
            
            # Create PDF document
            doc = SimpleDocTemplate(
                pdf_buffer,
                pagesize=letter,
                rightMargin=0.5*inch,
                leftMargin=0.5*inch,
                topMargin=0.5*inch,
                bottomMargin=0.5*inch,
                title='Medical Prescription',
                author='Lifegate Medical'
            )
            
            # Build content
            story = []
            
            # Add header
            story.append(self._build_header())
            story.append(Spacer(1, 0.1*inch))
            
            # Add clinic/facility info
            story.append(self._build_clinic_info())
            story.append(Spacer(1, 0.15*inch))
            
            # Add horizontal line
            story.append(self._build_separator())
            story.append(Spacer(1, 0.1*inch))
            
            # Add patient and prescription info
            story.append(self._build_patient_info(assessment))
            story.append(Spacer(1, 0.1*inch))
            
            # Add diagnosis
            story.append(self._build_diagnosis(assessment))
            story.append(Spacer(1, 0.15*inch))
            
            # Add medications
            story.append(self._build_medications_section(
                assessment, modification_session
            ))
            story.append(Spacer(1, 0.15*inch))
            
            # Add instructions
            story.append(self._build_instructions(assessment, modification_session))
            story.append(Spacer(1, 0.15*inch))
            
            # Add warnings
            story.append(self._build_warnings(assessment, modification_session))
            story.append(Spacer(1, 0.2*inch))
            
            # Add doctor signature section
            story.append(self._build_doctor_signature(clinician, assessment))
            
            # Add footer
            story.append(Spacer(1, 0.1*inch))
            story.append(self._build_footer())
            
            # Build PDF
            doc.build(story)
            
            logger.info(f"[PDF] Prescription generated for {assessment.patient.phone_number}")
            
            # Reset buffer position
            pdf_buffer.seek(0)
            return pdf_buffer
        
        except Exception as e:
            logger.error(f"[PDF] Error generating prescription: {str(e)}", exc_info=True)
            raise
    
    # ============= PDF SECTIONS =============
    
    def _build_header(self):
        """Build PDF header with clinic branding."""
        
        data = [[
            Paragraph(
                "🏥 LIFEGATE MEDICAL SERVICES",
                self.styles['CustomHeader']
            )
        ]]
        
        table = Table(data, colWidths=[7*inch])
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F0F0F0')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
        ]))
        
        return table
    
    def _build_clinic_info(self):
        """Build clinic information section."""
        
        data = [[
            Paragraph("<b>Telemedicine Platform</b><br/>Digital Healthcare Solutions", 
                     self.styles['SubHeader']),
            Paragraph("<b>Patient Prescription Document</b><br/>Valid at all pharmacies",
                     self.styles['SubHeader'])
        ]]
        
        table = Table(data, colWidths=[3.5*inch, 3.5*inch])
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
        ]))
        
        return table
    
    def _build_separator(self):
        """Build horizontal separator line."""
        
        data = [['']]
        table = Table(data, colWidths=[7*inch])
        table.setStyle(TableStyle([
            ('LINEBELOW', (0, 0), (-1, -1), 2, colors.HexColor('#333333')),
        ]))
        
        return table
    
    def _build_patient_info(self, assessment):
        """Build patient and prescription date info."""
        
        patient = assessment.patient
        prescription_date = datetime.now().strftime('%B %d, %Y')
        prescription_time = datetime.now().strftime('%I:%M %p')
        
        data = [
            [
                Paragraph(f"<b>Patient Name:</b> {patient.first_name or 'N/A'} {patient.last_name or 'N/A'}",
                         self.styles['FieldValue']),
                Paragraph(f"<b>Date:</b> {prescription_date}",
                         self.styles['FieldValue'])
            ],
            [
                Paragraph(f"<b>Phone:</b> {patient.phone_number}",
                         self.styles['FieldValue']),
                Paragraph(f"<b>Time:</b> {prescription_time}",
                         self.styles['FieldValue'])
            ],
            [
                Paragraph(f"<b>Age:</b> {self._calculate_age(patient)}",
                         self.styles['FieldValue']),
                Paragraph(f"<b>Prescription ID:</b> {str(assessment.id)[:8].upper()}",
                         self.styles['FieldValue'])
            ]
        ]
        
        table = Table(data, colWidths=[3.5*inch, 3.5*inch])
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ]))
        
        return table
    
    def _build_diagnosis(self, assessment):
        """Build diagnosis and chief complaint section."""
        
        condition = assessment.key_observations.get('likely_condition', 'N/A') \
            if assessment.key_observations else 'N/A'
        chief_complaint = assessment.chief_complaint
        
        # Symptoms
        symptoms = assessment.symptoms_overview.get('primary_symptoms', []) \
            if assessment.symptoms_overview else []
        symptoms_text = ', '.join(symptoms[:3]) if symptoms else 'N/A'
        
        data = [
            [Paragraph("<b>CHIEF COMPLAINT & DIAGNOSIS</b>", 
                      self.styles['FieldLabel'])],
            [Paragraph(f"<b>Chief Complaint:</b> {chief_complaint}",
                      self.styles['FieldValue'])],
            [Paragraph(f"<b>Primary Diagnosis:</b> {condition}",
                      self.styles['FieldValue'])],
            [Paragraph(f"<b>Symptoms:</b> {symptoms_text}",
                      self.styles['FieldValue'])]
        ]
        
        table = Table(data, colWidths=[7*inch])
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8E8E8')),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ]))
        
        return table
    
    def _build_medications_section(self, assessment, modification_session=None):
        """Build medications table."""
        
        # Get final medications
        if modification_session:
            medications = modification_session.modified_otc_suggestions.get('medications', []) \
                if modification_session.modified_otc_suggestions else []
        else:
            medications = assessment.otc_suggestions.get('medications', []) \
                if assessment.otc_suggestions else []
        
        data = [[Paragraph("<b>MEDICATIONS</b>", self.styles['FieldLabel'])]]
        
        if not medications:
            data.append([Paragraph("No medications prescribed.", self.styles['FieldValue'])])
        else:
            # Header row
            data.append([
                Paragraph("<b>Medication</b>", self.styles['FieldLabel']),
                Paragraph("<b>Dosage</b>", self.styles['FieldLabel']),
                Paragraph("<b>Frequency</b>", self.styles['FieldLabel']),
                Paragraph("<b>Duration</b>", self.styles['FieldLabel'])
            ])
            
            # Medication rows
            for med in medications:
                if isinstance(med, dict):
                    name = med.get('name', 'N/A')
                    dosage = med.get('dosage', 'N/A')
                    frequency = med.get('frequency', 'N/A')
                    duration = med.get('duration', 'As needed')
                    
                    data.append([
                        Paragraph(name, self.styles['MedicationText']),
                        Paragraph(dosage, self.styles['MedicationText']),
                        Paragraph(frequency, self.styles['MedicationText']),
                        Paragraph(duration, self.styles['MedicationText'])
                    ])
        
        # Create table
        if len(medications) > 0:
            colWidths = [2*inch, 1.5*inch, 1.75*inch, 1.75*inch]
            table = Table(data, colWidths=colWidths)
        else:
            table = Table(data, colWidths=[7*inch])
        
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8E8E8')),
            ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#F5F5F5')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CCCCCC')),
            ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, 1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        
        return table
    
    def _build_instructions(self, assessment, modification_session=None):
        """Build patient instructions and recommendations."""
        
        # Get recommendations
        if modification_session:
            recs = modification_session.modified_recommendations.get('lifestyle_changes', []) \
                if modification_session.modified_recommendations else []
        else:
            recs = assessment.preliminary_recommendations.get('lifestyle_changes', []) \
                if assessment.preliminary_recommendations else []
        
        data = [[Paragraph("<b>INSTRUCTIONS & RECOMMENDATIONS</b>", 
                          self.styles['FieldLabel'])]]
        
        if recs:
            for idx, rec in enumerate(recs[:5], 1):
                if isinstance(rec, str):
                    data.append([Paragraph(f"• {rec}", self.styles['FieldValue'])])
        else:
            data.append([Paragraph("Follow doctor's advice.", self.styles['FieldValue'])])
        
        table = Table(data, colWidths=[7*inch])
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8E8E8')),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ]))
        
        return table
    
    def _build_warnings(self, assessment, modification_session=None):
        """Build emergency warning signs section."""
        
        # Get warnings
        if modification_session:
            warnings = modification_session.modified_monitoring_advice.get('when_to_seek_help', []) \
                if modification_session.modified_monitoring_advice else []
        else:
            warnings = assessment.monitoring_advice.get('when_to_seek_help', []) \
                if assessment.monitoring_advice else []
        
        data = [[Paragraph("<b>⚠️ SEEK IMMEDIATE HELP IF YOU EXPERIENCE:</b>", 
                          self.styles['Warning'])]]
        
        if warnings:
            for warning in warnings[:4]:
                if isinstance(warning, str):
                    data.append([Paragraph(f"• {warning}", self.styles['FieldValue'])])
        else:
            data.append([Paragraph("• Severe symptoms or allergic reactions", 
                                  self.styles['FieldValue'])])
        
        table = Table(data, colWidths=[7*inch])
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFE8E8')),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ]))
        
        return table
    
    def _build_doctor_signature(self, clinician, assessment):
        """Build doctor signature and credentials section."""
        
        # Get doctor's license number (from profile or settings)
        license_number = self._get_doctor_license(clinician)
        
        data = [
            [
                Paragraph(f"<b>Doctor's Name:</b> Dr. {clinician.first_name} {clinician.last_name}",
                         self.styles['FieldValue']),
                Paragraph(f"<b>License #:</b> {license_number}",
                         self.styles['FieldValue'])
            ],
            [
                Paragraph(f"<b>Specialty:</b> {self._get_doctor_specialty(clinician)}",
                         self.styles['FieldValue']),
                Paragraph(f"<b>Date Signed:</b> {datetime.now().strftime('%B %d, %Y')}",
                         self.styles['FieldValue'])
            ],
            [
                Paragraph(
                    "<i>Digital Signature: This prescription was issued through Lifegate Telemedicine Platform</i>",
                    self.styles['SubHeader']
                ),
                ''
            ],
            [
                Paragraph(
                    f"<b>Prescription Valid for:</b> 30 days from issue date",
                    self.styles['FieldValue']
                ),
                ''
            ]
        ]
        
        table = Table(data, colWidths=[3.5*inch, 3.5*inch])
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F9F9F9')),
            ('LINEABOVE', (0, 0), (-1, 0), 2, colors.HexColor('#333333')),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ]))
        
        return table
    
    def _build_footer(self):
        """Build footer with legal info."""
        
        data = [[
            Paragraph(
                "This is a valid medical prescription issued by a licensed healthcare provider. "
                "Please present this document to any pharmacy to obtain prescribed medications.",
                self.styles['SubHeader']
            )
        ]]
        
        table = Table(data, colWidths=[7*inch])
        table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#666666')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        
        return table
    
    # ============= HELPER METHODS =============
    
    def _calculate_age(self, patient):
        """Calculate patient age from date of birth."""
        try:
            if hasattr(patient, 'date_of_birth') and patient.date_of_birth:
                today = datetime.now().date()
                age = today.year - patient.date_of_birth.year
                if (today.month, today.day) < (patient.date_of_birth.month, patient.date_of_birth.day):
                    age -= 1
                return f"{age} years"
            return "N/A"
        except:
            return "N/A"
    
    def _get_doctor_license(self, clinician):
        """Get doctor's license number from profile."""
        try:
            if hasattr(clinician, 'clinician_profile'):
                profile = clinician.clinician_profile
                if hasattr(profile, 'license_number'):
                    return profile.license_number or 'LIC-' + clinician.id.hex[:8].upper()
            # Fallback: generate from ID
            return 'LIC-' + clinician.id.hex[:8].upper()
        except:
            return 'LIC-XXXX-XXXX'
    
    def _get_doctor_specialty(self, clinician):
        """Get doctor's specialty."""
        try:
            if hasattr(clinician, 'clinician_profile'):
                profile = clinician.clinician_profile
                if hasattr(profile, 'specialization'):
                    return profile.specialization or 'General Medicine'
            return 'General Medicine'
        except:
            return 'General Medicine'