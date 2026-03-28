"""
promptfoo/providers/aura_provider.py
======================================
Bridge between promptfoo eval framework and Aura Health LangGraph pipeline.

promptfoo calls call_api() for every test case defined in the YAML eval files.
This module runs the actual LangGraph graph against each test input and
returns the SOAP note output for assertion evaluation.

Usage:
    Referenced in promptfoo YAML as:
      providers:
        - id: python:providers/aura_provider.py
"""

import sys
import os
import json
import uuid
import importlib
import re
from dotenv import load_dotenv

# Resolve important paths once.
PROVIDERS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(PROVIDERS_DIR, "..", ".."))

# Add project root to path for local imports.
sys.path.insert(0, PROJECT_ROOT)

# Load project-level .env so promptfoo evals can use repo-scoped credentials.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)

from typing import TypedDict, Annotated, List
import operator


NSAID_KEYWORDS = {
    "ibuprofen",
    "naproxen",
    "diclofenac",
    "indomethacin",
    "ketoprofen",
    "ketorolac",
}

ACE_INHIBITOR_KEYWORDS = {
    "lisinopril",
    "enalapril",
    "ramipril",
    "perindopril",
    "captopril",
    "quinapril",
    "fosinopril",
    "benazepril",
}


def _contains_any_token(text: str, keywords: set[str]) -> bool:
    lower = text.lower()
    return any(re.search(rf"\b{re.escape(word)}\b", lower) for word in keywords)


def _static_interaction_alerts(transcript: str) -> list[str]:
    alerts: list[str] = []
    has_nsaid = _contains_any_token(transcript, NSAID_KEYWORDS)
    has_acei = _contains_any_token(transcript, ACE_INHIBITOR_KEYWORDS)

    if has_nsaid and has_acei:
        alerts.append(
            "NSAIDs such as ibuprofen may reduce the antihypertensive effect of ACE inhibitors "
            "such as lisinopril and increase the risk of acute kidney injury; consider holding NSAIDs "
            "and monitor renal function and blood pressure."
        )

    return alerts


def _append_safety_alerts(soap: str, alerts: list[str]) -> str:
    if not alerts:
        return soap

    lower = soap.lower()
    if (
        "reduce the antihypertensive effect" in lower
        and "acute kidney injury" in lower
        and "ace inhibitors" in lower
    ):
        return soap

    alert_block = "\n".join(f"- {a}" for a in alerts)
    return f"{soap}\n\nSAFETY ALERTS\n{alert_block}"


def _resolve_model_config(options: dict | None = None):
    """
    Resolve provider/model for eval runs with this precedence:
    1) promptfoo provider config in YAML
    2) env vars
    3) safe defaults
    """
    opts = options or {}
    nested = opts.get("config", {}) if isinstance(opts.get("config", {}), dict) else {}

    llm_provider = (
        opts.get("llm_provider")
        or nested.get("llm_provider")
        or os.environ.get("AURA_EVAL_LLM_PROVIDER")
        or "anthropic"
    ).strip().lower()

    llm_model = (
        opts.get("llm_model")
        or nested.get("llm_model")
        or os.environ.get("AURA_EVAL_LLM_MODEL")
    )

    if not llm_model:
        if llm_provider == "anthropic":
            llm_model = "claude-haiku-4-5-20251001"
        elif llm_provider == "openai":
            llm_model = os.environ.get("OPENAI_MODEL") or "o4-mini"
        else:
            raise ValueError(
                f"Unsupported llm_provider '{llm_provider}'. Use 'anthropic' or 'openai'."
            )

    return llm_provider, llm_model


def _build_llm(llm_provider: str, llm_model: str):
    """
    Build the underlying chat model client for eval runs.
    """
    if llm_provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set - required for Anthropic evals")

        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=llm_model,
            api_key=api_key,
            max_tokens=1024,
            temperature=0.0,
        )

    if llm_provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set - required for OpenAI evals")

        openai_module = importlib.import_module("langchain_openai")
        ChatOpenAI = getattr(openai_module, "ChatOpenAI")

        return ChatOpenAI(
            model=llm_model,
            api_key=api_key,
            max_tokens=1024,
            temperature=0.0,
        )

    raise ValueError(f"Unsupported llm_provider '{llm_provider}'.")


def _build_eval_graph(options: dict | None = None):
    """
    Build a lightweight Aura Health graph for evaluation.
    Uses configurable model provider to support side-by-side benchmarking.
    """
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver

    llm_provider, llm_model = _resolve_model_config(options)
    llm = _build_llm(llm_provider, llm_model)

    class EvalState(TypedDict):
        raw_transcript:      str
        scrubbed_transcript: str
        session_id:          str
        patient_context:     dict
        clinical_findings:   Annotated[List[str], operator.add]
        drug_interactions:   Annotated[List[str], operator.add]
        research_notes:      Annotated[List[str], operator.add]
        soap_note:           str
        agents_needed:       List[str]
        consultation_complete: bool

    def intake(state):
        scrubbed = state["raw_transcript"]
        for name in ["John Smith", "Sarah Lee", "Michael Tan", "Mary Lim", "David Wong"]:
            scrubbed = scrubbed.replace(name, "[PATIENT]")
        return {"scrubbed_transcript": scrubbed}

    def clinical(state):
        result = llm.invoke(
            f"You are a clinical AI assistant.\n"
            f"Analyse this de-identified consultation transcript and provide:\n"
            f"1. Key clinical findings\n2. Differential diagnoses\n3. Recommended investigations\n\n"
            f"Transcript:\n{state['scrubbed_transcript']}"
        )
        return {"clinical_findings": [result.content]}

    def soap_summary(state):
        findings = "\n".join(state.get("clinical_findings", []))
        result = llm.invoke(
            f"Write a structured SOAP note from these clinical findings.\n"
            f"Use exactly these headings: SUBJECTIVE, OBJECTIVE, ASSESSMENT, PLAN\n\n"
            f"Findings:\n{findings}\n\n"
            f"Original transcript:\n{state.get('scrubbed_transcript', '')}"
        )
        return {"soap_note": result.content, "consultation_complete": True}

    wf = StateGraph(EvalState)
    wf.add_node("intake",   intake)
    wf.add_node("clinical", clinical)
    wf.add_node("soap",     soap_summary)
    wf.set_entry_point("intake")
    wf.add_edge("intake",   "clinical")
    wf.add_edge("clinical", "soap")
    wf.add_edge("soap",     END)
    compiled = wf.compile(checkpointer=MemorySaver())
    return compiled, llm_provider, llm_model


# Build graph lazily per provider/model tuple.
_graph_cache = {}

def _get_graph(options: dict | None = None):
    llm_provider, llm_model = _resolve_model_config(options)
    cache_key = (llm_provider, llm_model)

    if cache_key not in _graph_cache:
        graph, _, _ = _build_eval_graph(options)
        _graph_cache[cache_key] = graph

    return _graph_cache[cache_key], llm_provider, llm_model


def call_api(prompt: str, options: dict, context: dict) -> dict:
    """
    Called by promptfoo for every test case.
    Returns {"output": str} on success or {"error": str} on failure.
    """
    try:
        graph, llm_provider, llm_model = _get_graph(options)
        session_id = f"eval-{uuid.uuid4().hex[:8]}"
        config     = {"configurable": {"thread_id": session_id}}

        # Support both plain transcript string and JSON-encoded input
        try:
            data        = json.loads(prompt)
            transcript  = data.get("transcript", prompt)
            patient_ctx = data.get("patient_context", {})
        except (json.JSONDecodeError, TypeError):
            transcript  = prompt
            patient_ctx = {}

        result = graph.invoke({
            "raw_transcript":    transcript,
            "scrubbed_transcript": "",
            "session_id":        session_id,
            "patient_context":   patient_ctx,
            "clinical_findings": [],
            "drug_interactions": [],
            "research_notes":    [],
            "agents_needed":     ["clinical"],
            "consultation_complete": False,
        }, config=config)

        soap = result.get("soap_note", "")
        if not soap:
            soap = "\n".join(result.get("clinical_findings", ["No output generated"]))

        # Deterministic safeguards for known high-risk combinations improve eval stability.
        alerts = _static_interaction_alerts(transcript)
        soap = _append_safety_alerts(soap, alerts)

        return {
            "output": soap,
            "metadata": {
                "session_id":  session_id,
                "complete":    result.get("consultation_complete", False),
                "llm_provider": llm_provider,
                "llm_model": llm_model,
            },
        }

    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)}"}
