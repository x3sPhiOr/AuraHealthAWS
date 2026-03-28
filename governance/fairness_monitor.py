"""
governance/fairness_monitor.py
================================
IMDA Model AI Governance Framework — Principle 3: Fairness, Equity & Non-Discrimination
Singapore PDPA: Protection of personal data including race, religion, health status
AI Verify: Fairness metrics

Scans every SOAP note for language that could indicate biased clinical
reasoning based on protected characteristics under Singapore law.
"""

import re
from typing import List, Tuple


# Protected characteristics under Singapore PDPA and anti-discrimination guidelines
PROTECTED_CHARACTERISTICS = {
    "race":       ["chinese", "malay", "indian", "eurasian", "caucasian", "ethnic"],
    "religion":   ["muslim", "christian", "buddhist", "hindu", "sikh", "taoist", "catholic"],
    "gender":     ["male", "female", "man", "woman", "transgender"],
    "age":        ["elderly", "old age", "geriatric", "young patient", "senior citizen"],
    "disability": ["disabled", "disability", "handicapped", "impaired"],
    "nationality": ["foreign worker", "migrant", "expat", "pr holder"],
}

# Regex patterns for potentially biased clinical assumptions
BIAS_PATTERNS = [
    r"(chinese|malay|indian|eurasian).{0,30}(tend|typical|usual|often|more likely)",
    r"given (his|her|their).{0,20}(race|ethnicity|religion|background)",
    r"(elderly|old|senior).{0,30}(non.?compli|difficult|refuse)",
    r"drug.{0,5}seeking",
    r"frequent flyer",
    r"(low|poor).{0,10}(compliance|adherence).{0,30}(background|culture|race)",
]


def check_fairness(soap_note: str) -> Tuple[bool, List[dict]]:
    """
    Scans SOAP note for potentially biased language patterns.
    Returns (is_fair, list_of_issues).
    """
    issues  = []
    lower   = soap_note.lower()

    for pattern in BIAS_PATTERNS:
        matches = re.findall(pattern, lower)
        if matches:
            issues.append({
                "type":     "potential_bias_language",
                "pattern":  pattern,
                "match":    str(matches[0]),
                "severity": "medium",
                "guidance": (
                    "Review whether demographic assumptions are influencing "
                    "clinical reasoning. Ensure clinical decisions are based on "
                    "medical evidence, not demographic stereotypes."
                ),
            })

    # Check if protected characteristic terms appear in ASSESSMENT or PLAN
    # without clinical justification (e.g. known genetic predispositions are acceptable)
    for characteristic, terms in PROTECTED_CHARACTERISTICS.items():
        for term in terms:
            if term in lower:
                # Find where in the note it appears
                idx = lower.find(term)
                surrounding = soap_note[max(0, idx - 300): idx + 300].upper()
                if any(section in surrounding for section in ["ASSESSMENT", "PLAN"]):
                    issues.append({
                        "type":           "demographic_term_in_clinical_section",
                        "characteristic": characteristic,
                        "term":           term,
                        "severity":       "low",
                        "guidance": (
                            f"The term '{term}' appears in a clinical section. "
                            "Verify this reference is medically justified "
                            "(e.g. genetic predisposition) rather than an assumption. "
                            "Per PDPA and IMDA guidelines, protected characteristics "
                            "should not drive clinical decisions."
                        ),
                    })
                    break  # one issue per characteristic per note

    return (len(issues) == 0, issues)


def fairness_node(state: dict) -> dict:
    """LangGraph node — runs fairness audit and adds results to state."""
    is_fair, issues = check_fairness(state.get("soap_note", ""))
    return {
        "fairness_passed": is_fair,
        "fairness_issues": issues,
        "pdpa_compliant":  True,   # PII already scrubbed upstream by intake_node
    }
