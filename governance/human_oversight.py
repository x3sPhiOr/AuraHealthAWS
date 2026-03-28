"""
governance/human_oversight.py
==============================
IMDA Model AI Governance Framework — Principle 1: Human Oversight & Control
Singapore MOH AI in Healthcare Guidelines: Clinical Decision Support (CDS)

Classifies every consultation output into a required oversight level so
that clinicians know exactly how much scrutiny is needed before acting.
"""

from enum import Enum
from typing import List


class OversightLevel(Enum):
    AUTONOMOUS = "autonomous"   # informational only — no clinical action
    ADVISORY   = "advisory"     # doctor should review before acting
    MANDATORY  = "mandatory"    # doctor must countersign before entry
    ESCALATE   = "escalate"     # immediate clinical review required


# Red-flag terms that always trigger immediate escalation
ESCALATION_TRIGGERS = [
    "chest pain", "troponin", "nstemi", "stemi", "sepsis", "septic",
    "stroke", "tia", "anaphylaxis", "anaphylactic", "overdose",
    "suicidal", "self-harm", "emergency", "critical", "urgent admission",
    "respiratory failure", "cardiac arrest", "altered consciousness",
]

# Terms requiring mandatory doctor countersignature
MANDATORY_REVIEW_TRIGGERS = [
    "hypertension stage 2", "uncontrolled hypertension",
    "diabetes", "chronic kidney disease", "ckd",
    "heart failure", "antibiotic", "controlled drug",
    "opioid", "benzodiazepine", "steroid", "insulin",
    "warfarin", "heparin", "anticoagulant",
]


def determine_oversight_level(soap_note: str, confidence: float) -> OversightLevel:
    """
    Risk-proportionate oversight classification per IMDA framework.
    Higher clinical risk → higher required oversight level.
    """
    note_lower = soap_note.lower()

    if any(t in note_lower for t in ESCALATION_TRIGGERS):
        return OversightLevel.ESCALATE

    if confidence < 0.65:
        return OversightLevel.MANDATORY

    if any(t in note_lower for t in MANDATORY_REVIEW_TRIGGERS):
        return OversightLevel.MANDATORY

    return OversightLevel.ADVISORY


def human_oversight_node(state: dict) -> dict:
    """
    LangGraph node — classifies required oversight level and adds
    clear human-readable instructions to the state.
    """
    soap = state.get("soap_note", "")
    xai  = state.get("xai_record", {})
    conf = xai.get("confidence_score", 0.5) if isinstance(xai, dict) else 0.5

    level = determine_oversight_level(soap, conf)

    instructions = {
        OversightLevel.ESCALATE: (
            "CLINICAL ESCALATION REQUIRED: This note contains findings that require "
            "immediate clinical review. Contact the senior clinician or on-call team "
            "before any action is taken. Do not act on this note unilaterally."
        ),
        OversightLevel.MANDATORY: (
            "MANDATORY REVIEW REQUIRED: This AI-generated note must be reviewed "
            "and countersigned by a licensed doctor before entry into the medical record. "
            "Do not use this note for clinical decisions without doctor approval."
        ),
        OversightLevel.ADVISORY: (
            "ADVISORY REVIEW: Please review this AI-generated note. You may edit, "
            "amend, or reject any section. Your clinical judgement takes precedence."
        ),
        OversightLevel.AUTONOMOUS: (
            "INFORMATIONAL: This output is for reference only and should not be used "
            "for direct clinical decisions without professional review."
        ),
    }

    return {
        "oversight_level":        level.value,
        "oversight_instructions": instructions[level],
        "human_review_required":  level in (OversightLevel.MANDATORY, OversightLevel.ESCALATE),
        "escalation_required":    level == OversightLevel.ESCALATE,
    }
