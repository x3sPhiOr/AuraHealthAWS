"""
governance/clinical_safety_guard.py
=====================================
Singapore Ministry of Health (MOH) AI in Healthcare Guidelines
Singapore Health Sciences Authority (HSA) — Software as Medical Device (SaMD) Class B
IMDA Model AI Governance Framework — Principle 6: Robustness & Security

Applies clinical safety thresholds and always appends the MOH-mandated
disclosure to every output. Blocks outputs below minimum confidence threshold.
"""

CONFIDENCE_THRESHOLDS = {
    "block":    0.40,   # below this → block output, require manual note
    "warn":     0.65,   # below this → prominent warning banner
    "advisory": 0.80,   # below this → standard advisory
    "accept":   0.80,   # at or above → standard output
}

MOH_DISCLAIMER = """
---
## SINGAPORE MINISTRY OF HEALTH — AI CLINICAL DECISION SUPPORT DISCLOSURE

| Field | Details |
|-------|---------|
| System | Aura Health Clinical Decision Support |
| Regulatory status | Clinical Decision Support Tool (not a diagnostic device) |
| HSA classification | Software as Medical Device — Class B CDS |
| Framework | IMDA Model AI Governance Framework 2nd Edition |
| Intended use | Assist licensed healthcare professionals with documentation |
| Not for | Autonomous diagnosis, prescription, or treatment without oversight |

**The treating clinician is solely responsible for all clinical decisions.**
This AI output must be reviewed by a licensed Singapore-registered doctor
before entry into any medical record or use in patient care.

*Adverse event reporting: aurahealth-safety@example.com*
"""


def clinical_safety_guard_node(state: dict) -> dict:
    """
    LangGraph node — applies MOH safety thresholds and appends disclosure.

    Blocks output if confidence is below minimum threshold.
    Adds warning banner for low-medium confidence.
    Always appends MOH regulatory disclaimer.
    """
    xai  = state.get("xai_record", {}) or {}
    conf = xai.get("confidence_score", 0.5)
    soap = state.get("soap_note", "")

    # Block output if below minimum threshold
    if conf < CONFIDENCE_THRESHOLDS["block"]:
        blocked_note = (
            f"## OUTPUT BLOCKED — CONFIDENCE BELOW MINIMUM THRESHOLD\n\n"
            f"**Confidence score:** {conf:.0%}  \n"
            f"**Minimum required:** {CONFIDENCE_THRESHOLDS['block']:.0%}\n\n"
            "The AI was unable to generate a sufficiently reliable clinical note "
            "from the available transcript. This may be due to:\n"
            "- Insufficient clinical detail in the transcript\n"
            "- Ambiguous or conflicting clinical information\n"
            "- Content outside the system's validated scope\n\n"
            "**Action required:** Please document this consultation manually.\n"
            + MOH_DISCLAIMER
        )
        return {
            "soap_note":      blocked_note,
            "output_blocked": True,
            "block_reason":   f"Confidence {conf:.0%} below block threshold {CONFIDENCE_THRESHOLDS['block']:.0%}",
            "moh_compliant":  True,
            "samd_class":     "Class B — Clinical Decision Support",
        }

    # Add warning banner for low confidence
    if conf < CONFIDENCE_THRESHOLDS["warn"]:
        warning = (
            f"## LOW CONFIDENCE WARNING\n\n"
            f"**Confidence: {conf:.0%}** — This note was generated with low confidence. "
            "Please review every section carefully before clinical use.\n\n"
        )
        soap = warning + soap

    # Always append MOH disclaimer
    soap = soap + MOH_DISCLAIMER

    return {
        "soap_note":      soap,
        "output_blocked": False,
        "moh_compliant":  True,
        "samd_class":     "Class B — Clinical Decision Support",
    }
