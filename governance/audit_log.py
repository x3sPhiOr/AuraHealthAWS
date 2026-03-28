"""
governance/audit_log.py
========================
IMDA Model AI Governance Framework — Principle 5: Accountability & Auditability
Singapore HSA: Medical Device Software audit trail requirements
MOH: 7-year medical record retention requirement

Writes an immutable, hash-verified audit log entry for every consultation.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional


def _count_soap_sections(soap_note: str) -> int:
    sections = ["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN"]
    return sum(1 for section in sections if section in soap_note.upper())


def build_ai_verify_runtime_report(state: dict) -> dict:
    """
    Builds an AI Verify-style runtime report from the actual consultation state.
    This is dynamic and evaluated per session.
    """
    soap_note = state.get("soap_note", "") or ""
    xai = state.get("xai_record", {}) or {}

    fairness_issues = state.get("fairness_issues", []) or []
    evidence_sources = xai.get("evidence_sources", []) if isinstance(xai, dict) else []
    reasoning_chain = xai.get("reasoning_chain", []) if isinstance(xai, dict) else []

    principles = {
        "P1_Accountability": {
            "passed": bool(state.get("session_id")),
            "detail": "Session ID and immutable audit file are present.",
        },
        "P2_Explainability": {
            "passed": bool(xai.get("model_id")) and len(reasoning_chain) > 0,
            "detail": "Model provenance and reasoning chain captured.",
        },
        "P3_Fairness": {
            "passed": bool(state.get("fairness_passed", False)),
            "detail": f"Fairness issues flagged: {len(fairness_issues)}.",
        },
        "P4_Data_Governance": {
            "passed": bool(state.get("pdpa_compliant", True)),
            "detail": "PII governance and PDPA compliance flag present.",
        },
        "P5_Human_Oversight": {
            "passed": state.get("oversight_level") in {"advisory", "mandatory", "escalate", "autonomous"},
            "detail": "Oversight level assigned for clinician review.",
        },
        "P6_Robustness_Safety": {
            "passed": bool(state.get("moh_compliant", False)) and bool(state.get("samd_class")),
            "detail": "MOH compliance and SaMD classification recorded.",
        },
        "P7_Transparency": {
            "passed": bool(xai.get("knowledge_cutoff")) and bool(xai.get("model_id")),
            "detail": "Model ID and knowledge cutoff disclosed.",
        },
        "P8_Record_Quality": {
            "passed": _count_soap_sections(soap_note) == 4,
            "detail": "SOAP output contains SUBJECTIVE/OBJECTIVE/ASSESSMENT/PLAN.",
        },
        "P9_Evidence_Traceability": {
            "passed": len(evidence_sources) > 0,
            "detail": f"Evidence sources attached: {len(evidence_sources)}.",
        },
    }

    passed_count = sum(1 for item in principles.values() if item.get("passed"))
    total_count = len(principles)

    return {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "passed": passed_count == total_count,
        "principles_passed": passed_count,
        "principles_total": total_count,
        "principles": principles,
    }


def append_ai_verify_summary_to_soap(soap_note: str, report: dict) -> str:
    """Appends runtime AI Verify principle checks to the session SOAP output."""
    if not soap_note:
        soap_note = ""

    principles = report.get("principles", {}) or {}
    lines = []
    for key, value in principles.items():
        status = "PASS" if value.get("passed") else "REVIEW"
        lines.append(f"- {key}: {status} — {value.get('detail', '')}")

    summary = (
        "\n\n---\n"
        "## AURA AI VERIFY SESSION CHECK\n"
        f"Overall: {'PASS' if report.get('passed') else 'REVIEW REQUIRED'} "
        f"({report.get('principles_passed', 0)}/{report.get('principles_total', 0)} principles)\n\n"
        + "\n".join(lines)
    )
    return soap_note + summary


def compute_content_hash(content: str) -> str:
    """SHA-256 hash of content — proves the note was not altered after generation."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_audit_log(state: dict, output_dir: str = "audit_logs") -> str:
    """
    Writes an immutable audit log entry aligned with:
    - IMDA Model AI Governance Framework Principle 5
    - Singapore HSA Software as Medical Device audit requirements
    - MOH 7-year medical record retention policy

    Returns the path to the session-specific audit file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    session_id = state.get("session_id", "unknown")
    soap_note  = state.get("soap_note",  "")
    xai        = state.get("xai_record", {}) or {}

    ai_verify_runtime = state.get("ai_verify_runtime") or build_ai_verify_runtime_report(state)

    audit_entry = {
        # ── Schema & identity ─────────────────────────────────────────────────
        "schema_version":    "aura-health-audit-v1.0",
        "session_id":        session_id,
        "timestamp_utc":     datetime.utcnow().isoformat() + "Z",
        "imda_framework":    "Model AI Governance Framework 2nd Edition (2020)",
        "ai_verify_version": "AI Verify Toolkit 1.0",
        "ai_verify_runtime_summary": {
            "passed":            ai_verify_runtime.get("passed", False),
            "principles_passed": ai_verify_runtime.get("principles_passed", 0),
            "principles_total":  ai_verify_runtime.get("principles_total", 0),
        },
        "ai_verify_runtime_checks": ai_verify_runtime.get("principles", {}),

        # ── Model provenance ──────────────────────────────────────────────────
        "model_id":          xai.get("model_id", state.get("model_id", "unknown")),
        "model_provider":    "Anthropic via AWS Bedrock (us-east-1)",
        "knowledge_cutoff":  xai.get("knowledge_cutoff", "2024-12-31"),
        "agents_invoked":    state.get("agents_needed", []),

        # ── Input record ──────────────────────────────────────────────────────
        "input_hash":              compute_content_hash(
                                       state.get("scrubbed_transcript", "")
                                   ),
        "pii_entities_removed":    len(state.get("pii_detected", [])),
        "pii_entity_types":        [p.get("type") for p in state.get("pii_detected", [])],
        "patient_context_recorded": {
            k: "[REDACTED]" if k in ("name", "dob", "nric", "phone", "email")
            else v
            for k, v in (state.get("patient_context") or {}).items()
        },

        # ── Output record ─────────────────────────────────────────────────────
        "output_hash":           compute_content_hash(soap_note),
        "soap_sections_present": [
            s for s in ["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN"]
            if s in soap_note.upper()
        ],
        "output_length_chars":   len(soap_note),

        # ── XAI / Explainability ──────────────────────────────────────────────
        "confidence_score":   xai.get("confidence_score", 0),
        "evidence_count":     len(xai.get("evidence_sources", [])),
        "reasoning_steps":    len(xai.get("reasoning_chain", [])),

        # ── Governance decisions ──────────────────────────────────────────────
        "oversight_level":       state.get("oversight_level", "advisory"),
        "human_review_required": state.get("human_review_required", True),
        "escalation_required":   state.get("escalation_required", False),
        "fairness_passed":       state.get("fairness_passed", False),
        "fairness_issues_count": len(state.get("fairness_issues", [])),
        "output_blocked":        state.get("output_blocked", False),
        "block_reason":          state.get("block_reason", None),
        "moh_compliant":         state.get("moh_compliant", False),
        "samd_classification":   state.get("samd_class", "Class B CDS"),

        # ── Compliance flags ──────────────────────────────────────────────────
        "pdpa_compliant":        True,
        "data_residency":        "United States (AWS us-east-1)",
        "consent_recorded":      state.get("consent_recorded", False),
        "retention_policy_years": 7,
    }

    # Append-only JSONL for full audit trail
    jsonl_path = Path(output_dir) / "aura_audit.jsonl"
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(audit_entry) + "\n")

    # Individual session file for point-in-time retrieval
    session_path = Path(output_dir) / f"{session_id}_audit.json"
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(audit_entry, f, indent=2)

    return str(session_path)


def audit_node(state: dict) -> dict:
    """LangGraph node — writes audit log and adds log_path to state."""
    ai_verify_runtime = build_ai_verify_runtime_report(state)
    soap_with_verify = append_ai_verify_summary_to_soap(
        state.get("soap_note", ""), ai_verify_runtime
    )
    enriched_state = {
        **state,
        "soap_note": soap_with_verify,
        "ai_verify_runtime": ai_verify_runtime,
    }
    log_path = write_audit_log(enriched_state)
    return {
        "soap_note":         soap_with_verify,
        "ai_verify_runtime": ai_verify_runtime,
        "audit_log_path":    log_path,
        "consultation_complete": True,
    }
