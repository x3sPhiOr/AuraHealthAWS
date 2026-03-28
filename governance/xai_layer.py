"""
governance/xai_layer.py
=======================
IMDA Model AI Governance Framework 2nd Edition — Principle 2: Explainability
AI Verify Principle: Explainability & Transparency

Attaches an ExplainabilityRecord to every SOAP note so that doctors,
auditors, and regulators can understand WHY the AI produced each output.
"""

import os
from datetime import datetime
from typing import TypedDict, List


class ExplainabilityRecord(TypedDict):
    """Attached to every SOAP note — satisfies IMDA explainability requirement."""
    session_id:        str
    timestamp:         str
    model_id:          str
    agents_invoked:    List[str]
    evidence_sources:  List[dict]
    confidence_score:  float
    reasoning_chain:   List[str]
    knowledge_cutoff:  str
    limitations:       List[str]
    imda_version:      str


def build_explainability_record(state: dict, model_id: str) -> ExplainabilityRecord:
    """
    Generates a full explainability record for every consultation.
    Satisfies IMDA Model AI Governance Framework 2nd Ed, Section 2.2.
    """
    evidence = []
    for finding in state.get("clinical_findings", []):
        evidence.append({
            "agent":   "clinical",
            "source":  "CDC/AHA Guidelines (local FAISS index)",
            "type":    "clinical_guideline",
            "excerpt": finding[:200] + "..." if len(finding) > 200 else finding,
        })
    for drug in state.get("drug_interactions", []):
        evidence.append({
            "agent":   "drug",
            "source":  "DrugBank / local formulary",
            "type":    "drug_database",
            "excerpt": drug[:200] + "..." if len(drug) > 200 else drug,
        })
    for note in state.get("research_notes", []):
        evidence.append({
            "agent":   "research",
            "source":  "PubMed / clinical evidence base",
            "type":    "research_literature",
            "excerpt": note[:200] + "..." if len(note) > 200 else note,
        })

    soap = state.get("soap_note", "")
    sections = sum(1 for s in ["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN"]
                   if s in soap.upper())
    confidence = round(sections / 4.0, 2)

    agents = state.get("agents_needed", [])

    return ExplainabilityRecord(
        session_id       = state.get("session_id", "unknown"),
        timestamp        = datetime.utcnow().isoformat() + "Z",
        model_id         = model_id,
        agents_invoked   = agents,
        evidence_sources = evidence,
        confidence_score = confidence,
        reasoning_chain  = [
            "Step 1: Transcript received and PII scrubbed via presidio (HIPAA Safe Harbor)",
            "Step 2: Supervisor agent analysed clinical content and selected specialist agents",
            f"Step 3: Agents invoked: {agents}",
            "Step 4: RAG retrieval from local clinical knowledge base (FAISS)",
            "Step 5: Each specialist agent produced findings independently",
            "Step 6: Summary agent synthesised all findings into SOAP format",
            "Step 7: XAI, fairness, oversight, and safety governance nodes applied",
        ],
        knowledge_cutoff = "2024-12-31",
        limitations      = [
            "AI-generated note is a DECISION SUPPORT tool only — not autonomous diagnosis",
            "Final clinical judgement rests solely with the licensed doctor",
            "Knowledge base may not reflect guidelines published after the cutoff date",
            "Not validated for paediatric, obstetric, or psychiatric cases",
            "Drug interaction list is not exhaustive — always cross-check with BNF/MIMS",
            "Performance may vary for non-English clinical language",
            "Confidence score is a structural heuristic, not a clinical validation metric",
        ],
        imda_version     = "IMDA Model AI Governance Framework 2nd Edition (2020)",
    )


def xai_node(state: dict) -> dict:
    """
    LangGraph node — builds and attaches explainability record.
    Appends IMDA-compliant disclosure footer to the SOAP note.
    Insert between summary_node and fairness_node in graph.
    """
    model_id = os.getenv("BEDROCK_MODEL", os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"))
    record   = build_explainability_record(state, model_id)

    disclaimer = f"""

---
## AURA HEALTH AI DISCLOSURE
### IMDA Model AI Governance Framework (2nd Edition) Compliant

| Field | Value |
|-------|-------|
| Model | {record['model_id']} |
| Confidence | {record['confidence_score'] * 100:.0f}% |
| Evidence sources | {len(record['evidence_sources'])} retrieved |
| Agents used | {', '.join(record['agents_invoked']) or 'clinical'} |
| Generated at | {record['timestamp']} |
| Knowledge cutoff | {record['knowledge_cutoff']} |

**IMPORTANT:** This AI-generated note is a clinical decision SUPPORT tool only.
The treating doctor retains full clinical responsibility and must review,
validate, and countersign this note before entry into any medical record.
"""
    return {
        "soap_note":  state.get("soap_note", "") + disclaimer,
        "xai_record": record,
    }
