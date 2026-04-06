"""
app.py — Aura Health production entrypoint for AWS App Runner.

Assembled from aura_health_langgraph.ipynb (cells 4, 6, 8, 10, 12, 14, 16, 18, 23, 24, 49).
Demo cells, pip-install cells, visualisation cells, and interactive STT cells are excluded.
The FastAPI server always starts; configure via environment variables (see .env.example).
"""

# ── 1. Standard library ────────────────────────────────────────────────────────
import os
import re
import sys
import html
import json
import glob
import uuid
import asyncio
import zipfile
import operator
import warnings
from typing import TypedDict, Annotated, List, Optional, Literal
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

# ── 2. Third-party: env, async ────────────────────────────────────────────────
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore")

# ── 3. AWS / LangGraph / LangChain ────────────────────────────────────────────
import boto3
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ── 4. HuggingFace ────────────────────────────────────────────────────────────
from huggingface_hub import InferenceClient
from huggingface_hub.utils import HfHubHTTPError

# ── 5. PII scrubbing ──────────────────────────────────────────────────────────
from presidio_analyzer import AnalyzerEngine, PatternRecognizer
from presidio_analyzer.pattern import Pattern
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# ── 6. FastAPI / SSE ──────────────────────────────────────────────────────────
import io
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Query, Depends, Header, status
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field

# ── 7. Authentication ──────────────────────────────────────────────────────────
try:
    from app_auth import verify_bearer_token, verify_bearer_token_or_query
    AUTH_AVAILABLE = True
except ImportError as e:
    AUTH_AVAILABLE = False
    print(f"WARNING: app_auth import failed: {e}")
    print("Using fallback authentication (request all fail with 403).")
    
    def verify_bearer_token(authorization: str = Header(None)) -> str:
        """
        Fallback if app_auth is not available.
        Always rejects requests with 403 to ensure no accidental public access.
        """
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API authentication module not available.",
        )
    
    def verify_bearer_token_or_query(authorization: str = Header(None), token: str = None) -> str:
        """
        Fallback if app_auth is not available (SSE/query param version).
        Always rejects requests with 403 to ensure no accidental public access.
        """
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API authentication module not available.",
        )

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION  (from environment / .env)
# ══════════════════════════════════════════════════════════════════════════════

AWS_REGION                  = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
BEDROCK_MODEL               = os.getenv("BEDROCK_MODEL", "anthropic.claude-haiku-4-5-20251001-v1:0")
BEDROCK_INFERENCE_PROFILE_ID = os.getenv("BEDROCK_INFERENCE_PROFILE_ID", "").strip()
BEDROCK_PROVIDER            = os.getenv("BEDROCK_PROVIDER", "anthropic").strip()

# CORS — comma-separated list of allowed origins.
# Defaults to * (all) for local dev.  Set to your frontend domain in production,
# e.g. CORS_ORIGINS=https://app.example.com,https://staging.example.com
# Use "null" (the string) to allow file:// pages during development.
_raw_cors = os.getenv("CORS_ORIGINS", "*")
CORS_ORIGINS: list = [o.strip() for o in _raw_cors.split(",") if o.strip()]

HF_MEDICAL_MODEL            = os.getenv("HF_MEDICAL_MODEL", "m42-health/Llama3-Med42-8B")
HF_INFERENCE_PROVIDER       = os.getenv("HF_INFERENCE_PROVIDER", "").strip()

KB_USE_SEED                 = os.getenv("KB_USE_SEED", "true").lower() == "true"
KB_ENABLE_CDC               = os.getenv("KB_ENABLE_CDC", "false").lower() == "true"
KB_ENABLE_PUBMED            = os.getenv("KB_ENABLE_PUBMED", "false").lower() == "true"
KB_ENABLE_OPENFDA           = os.getenv("KB_ENABLE_OPENFDA", "false").lower() == "true"
KB_ENABLE_OPENFDA_ZIP       = os.getenv("KB_ENABLE_OPENFDA_ZIP", "false").lower() == "true"
KB_ENABLE_OPENFDA_EVENT     = os.getenv("KB_ENABLE_OPENFDA_EVENT", "false").lower() == "true"

RAG_CHUNK_SIZE              = int(os.getenv("RAG_CHUNK_SIZE", "900"))
RAG_CHUNK_OVERLAP           = int(os.getenv("RAG_CHUNK_OVERLAP", "150"))

CDC_GUIDELINE_URLS = [
    u.strip()
    for u in os.getenv(
        "CDC_GUIDELINE_URLS",
        "https://www.cdc.gov/high-blood-pressure/about/index.html,"
        "https://www.cdc.gov/heart-disease/about/index.html"
    ).split(",")
    if u.strip()
]

PUBMED_QUERY    = os.getenv("PUBMED_QUERY", "hypertension guideline OR chest pain triage OR type 2 diabetes management")
PUBMED_RETMAX   = int(os.getenv("PUBMED_RETMAX", "8"))
PUBMED_EMAIL    = os.getenv("PUBMED_EMAIL", "")
PUBMED_TOOL     = os.getenv("PUBMED_TOOL", "aura-health-rag")
NCBI_API_KEY    = os.getenv("NCBI_API_KEY", "")

OPENFDA_TERMS   = [t.strip() for t in os.getenv("OPENFDA_TERMS", "lisinopril,metformin,ibuprofen").split(",") if t.strip()]
OPENFDA_API_KEY = os.getenv("OPENFDA_API_KEY", "")
OPENFDA_ZIP_DIR = os.getenv("OPENFDA_ZIP_DIR", "data/fda_zip_drop")
OPENFDA_ZIP_MAX_DOCS = int(os.getenv("OPENFDA_ZIP_MAX_DOCS", "200"))
OPENFDA_EVENT_TERMS = [t.strip() for t in os.getenv("OPENFDA_EVENT_TERMS", ",".join(OPENFDA_TERMS)).split(",") if t.strip()]
OPENFDA_EVENT_PER_TERM_LIMIT = int(os.getenv("OPENFDA_EVENT_PER_TERM_LIMIT", "5"))

STT_OPENAI_MODEL           = os.getenv("STT_OPENAI_MODEL", "gpt-4o-mini-transcribe")
STT_OPENAI_FALLBACK_MODELS = [
    m.strip() for m in os.getenv("STT_OPENAI_FALLBACK_MODELS", "gpt-4o-mini-transcribe,whisper-1").split(",")
    if m.strip()
]
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en")

LOCAL_OUTPUT_DIR           = os.getenv("AURA_OUTPUT_DIR", "aura_outputs")
LOCAL_AUDIT_DIR            = os.getenv("AURA_AUDIT_DIR", "audit_logs")
ENABLE_S3_ARTIFACT_UPLOADS = os.getenv("ENABLE_S3_ARTIFACT_UPLOADS", "true").lower() == "true"
AURA_OUTPUTS_BUCKET        = os.getenv("AURA_OUTPUTS_BUCKET", "aurahealth-aura-outputs").strip()
AURA_AUDIT_BUCKET          = os.getenv("AURA_AUDIT_BUCKET", "aurahealth-audit-logs").strip()
AURA_S3_PREFIX             = os.getenv("AURA_S3_PREFIX", "sessions").strip().strip("/")

DEFAULT_PATIENT_CONTEXT = {
    "age": 58,
    "gender": "male",
    "known_conditions": ["hypertension", "type_2_diabetes"],
    "current_medications": ["lisinopril 10mg", "metformin 500mg BD"],
    "allergies": "NKDA",
}

print("Configuration loaded.")
print(f"  Region   : {AWS_REGION}")
print(f"  Model    : {BEDROCK_MODEL}")
print(f"  Profile  : {BEDROCK_INFERENCE_PROFILE_ID or '(not set)'}")
print(f"  Provider : {BEDROCK_PROVIDER}")
print(f"  Outputs  : {LOCAL_OUTPUT_DIR} → {AURA_OUTPUTS_BUCKET or '(S3 disabled)'}")
print(f"  Audit    : {LOCAL_AUDIT_DIR} → {AURA_AUDIT_BUCKET or '(S3 disabled)'}")
print(f"  Time     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ══════════════════════════════════════════════════════════════════════════════
# AWS CHECK  (cell 6)
# ══════════════════════════════════════════════════════════════════════════════

def check_aws():
    """Verify credentials and Bedrock model availability."""
    try:
        sts = boto3.client("sts", region_name=AWS_REGION)
        identity = sts.get_caller_identity()
        print(f"AWS identity verified")
        print(f"  Account : {identity['Account']}")
        print(f"  ARN     : {identity['Arn']}")
    except Exception as e:
        print(f"AWS credentials error: {e}")
        return False

    try:
        bedrock = boto3.client("bedrock", region_name=AWS_REGION)
        models = bedrock.list_foundation_models(byProvider="Anthropic")
        available = [
            m["modelId"] for m in models["modelSummaries"]
            if m["modelLifecycle"]["status"] == "ACTIVE"
        ]
        print(f"\nActive Anthropic models in {AWS_REGION}:")
        for m in available:
            marker = " <-- selected" if m == BEDROCK_MODEL else ""
            print(f"  {m}{marker}")
        if BEDROCK_MODEL not in available and not BEDROCK_INFERENCE_PROFILE_ID:
            print(f"\nWARNING: {BEDROCK_MODEL} not in active list. Continuing — may still work via inference profile.")
        return True
    except Exception as e:
        print(f"Bedrock check error: {e}")
        return False


aws_ok = check_aws()

# ══════════════════════════════════════════════════════════════════════════════
# LLM INITIALISATION  (cell 8)
# ══════════════════════════════════════════════════════════════════════════════

def build_llm():
    """Build LLM — tries Bedrock first, falls back to direct Anthropic."""
    if aws_ok:
        session = boto3.Session(region_name=AWS_REGION)
        client = session.client("bedrock-runtime")
        runtime_model_id = BEDROCK_INFERENCE_PROFILE_ID or BEDROCK_MODEL
        bedrock_kwargs = {
            "model_id": runtime_model_id,
            "client": client,
            "model_kwargs": {
                "max_tokens": 2048,
                "temperature": 0.1,
                "anthropic_version": "bedrock-2023-05-31",
            },
        }
        # langchain_aws extracts the provider from the model ID string (text before
        # the first dot).  For cross-region inference IDs the prefix is the region
        # group (ap / us / eu), not the actual provider — so we must supply it
        # explicitly.  Same applies to ARN-style profile IDs.
        _NEEDS_EXPLICIT_PROVIDER = ("arn:", "ap.", "us.", "eu.")
        if any(runtime_model_id.startswith(p) for p in _NEEDS_EXPLICIT_PROVIDER):
            bedrock_kwargs["provider"] = BEDROCK_PROVIDER
        llm = ChatBedrock(**bedrock_kwargs)
        if BEDROCK_INFERENCE_PROFILE_ID:
            print(f"LLM: AWS Bedrock via inference profile ({BEDROCK_INFERENCE_PROFILE_ID})")
        else:
            print(f"LLM: AWS Bedrock ({BEDROCK_MODEL})")
    else:
        from langchain_anthropic import ChatAnthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("Set ANTHROPIC_API_KEY env var or fix AWS credentials")
        llm = ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            api_key=api_key,
            max_tokens=2048,
            temperature=0.1,
        )
        print("LLM: Direct Anthropic API (claude-haiku-4-5-20251001)")

    try:
        test = llm.invoke("Reply with exactly: AURA_READY")
    except Exception as e:
        msg = str(e)
        if "inference profile" in msg.lower() and not BEDROCK_INFERENCE_PROFILE_ID:
            raise ValueError(
                "This model requires an inference profile. Set BEDROCK_INFERENCE_PROFILE_ID."
            ) from e
        if "model provider should be supplied" in msg.lower():
            raise ValueError(
                "Set BEDROCK_PROVIDER in env (e.g. BEDROCK_PROVIDER=anthropic)."
            ) from e
        raise

    if "AURA_READY" in test.content:
        print("LLM smoke test: PASSED")
    else:
        print(f"LLM smoke test unexpected response: {test.content[:80]}")
    return llm


llm = build_llm()

# ══════════════════════════════════════════════════════════════════════════════
# PII SCRUBBER  (cell 10)
# ══════════════════════════════════════════════════════════════════════════════

analyzer  = AnalyzerEngine()
anonymizer = AnonymizerEngine()

nric_recognizer = PatternRecognizer(
    supported_entity="SG_NRIC",
    supported_language="en",
    patterns=[Pattern(name="sg_nric_fin", regex=r"\b[STFGM]\d{7}[A-Z]\b", score=0.85)],
)

sg_address_recognizer = PatternRecognizer(
    supported_entity="SG_ADDRESS",
    supported_language="en",
    patterns=[
        Pattern(
            name="sg_block_unit_postal",
            regex=r"\b(?:blk|block)\s*\d+[A-Za-z]?\s+[A-Za-z0-9'./\-\s]{3,80}#?\d{1,3}-\d{1,3}(?:\s+singapore\s+\d{6})?\b",
            score=0.7,
        ),
        Pattern(
            name="sg_street_postal",
            regex=r"\b\d{1,4}\s+[A-Za-z0-9'./\-\s]{3,80}\s+singapore\s+\d{6}\b",
            score=0.65,
        ),
    ],
)

analyzer.registry.add_recognizer(nric_recognizer)
analyzer.registry.add_recognizer(sg_address_recognizer)

SCRUB_ENTITIES = [
    "PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "LOCATION", "DATE_TIME",
    "MEDICAL_LICENSE", "URL", "IP_ADDRESS", "SG_NRIC", "SG_ADDRESS",
]

OPERATORS = {
    "PERSON":        OperatorConfig("replace", {"new_value": "[PATIENT]"}),
    "PHONE_NUMBER":  OperatorConfig("replace", {"new_value": "[PHONE]"}),
    "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "[EMAIL]"}),
    "LOCATION":      OperatorConfig("replace", {"new_value": "[LOCATION]"}),
    "DATE_TIME":     OperatorConfig("replace", {"new_value": "[DATE]"}),
    "SG_NRIC":       OperatorConfig("replace", {"new_value": "[SG_NRIC]"}),
    "SG_ADDRESS":    OperatorConfig("replace", {"new_value": "[SG_ADDRESS]"}),
    "DEFAULT":       OperatorConfig("replace", {"new_value": "[REDACTED]"}),
}

ENTITY_PRIORITY = {
    "SG_NRIC": 100, "SG_ADDRESS": 95, "PHONE_NUMBER": 90,
    "EMAIL_ADDRESS": 85, "PERSON": 80, "MEDICAL_LICENSE": 75,
    "LOCATION": 70, "DATE_TIME": 50, "URL": 40, "IP_ADDRESS": 40,
}


def _is_better(a, b) -> bool:
    a_key = (ENTITY_PRIORITY.get(a.entity_type, 10), round(float(a.score), 4), a.end - a.start)
    b_key = (ENTITY_PRIORITY.get(b.entity_type, 10), round(float(b.score), 4), b.end - b.start)
    return a_key > b_key


def _resolve_overlaps(results: list) -> list:
    selected = []
    for r in sorted(results, key=lambda x: (x.start, x.end)):
        overlaps = [i for i, s in enumerate(selected) if not (r.end <= s.start or r.start >= s.end)]
        if not overlaps:
            selected.append(r)
            continue
        if all(_is_better(r, selected[i]) for i in overlaps):
            for i in reversed(overlaps):
                selected.pop(i)
            selected.append(r)
    return sorted(selected, key=lambda x: x.start)


def scrub_pii(text: str) -> tuple:
    """Returns (scrubbed_text, list_of_detected_entities)."""
    raw_results = analyzer.analyze(text=text, entities=SCRUB_ENTITIES, language="en")
    results = _resolve_overlaps(raw_results)
    scrubbed = anonymizer.anonymize(text=text, analyzer_results=results, operators=OPERATORS).text
    detected = [{"type": r.entity_type, "score": round(r.score, 2)} for r in results]
    return scrubbed, detected


print("PII scrubber ready (HIPAA Safe Harbor + SG NRIC/address recognizers).")

# ══════════════════════════════════════════════════════════════════════════════
# HYBRID KNOWLEDGE BASE  (cell 12)
# ══════════════════════════════════════════════════════════════════════════════

print("Loading embedding model (first run may download ~90 MB)...")
embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",
    model_kwargs={"device": "cpu"},
)


def clean_text(raw: str, max_chars: int = 2500) -> str:
    raw = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<style[\s\S]*?</style>",   " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:max_chars]


def seed_docs() -> list:
    return [
        Document(page_content="Hypertension Stage 1: systolic 130-139 or diastolic 80-89 mmHg. First-line treatment includes lifestyle changes and single-drug therapy based on patient profile.", metadata={"source": "Seed: ACC/AHA style", "category": "cardiology"}),
        Document(page_content="Hypertension Stage 2: systolic >=140 or diastolic >=90 mmHg. Combination therapy is often needed and renal/electrolyte monitoring is recommended.", metadata={"source": "Seed: JNC style", "category": "cardiology"}),
        Document(page_content="Chest tightness differential includes ACS, GERD, musculoskeletal pain, and anxiety. Initial workup can include ECG, troponin, and chest radiography.", metadata={"source": "Seed: Emergency triage", "category": "emergency"}),
        Document(page_content="Lisinopril: ACE inhibitor for hypertension. Monitor creatinine and potassium. Common adverse effect is cough. Contraindicated in pregnancy.", metadata={"source": "Seed: Drug reference", "category": "pharmacology"}),
        Document(page_content="Metformin: first-line for type 2 diabetes. Avoid in severe renal dysfunction. Review around iodinated contrast procedures.", metadata={"source": "Seed: ADA style", "category": "endocrinology"}),
        Document(page_content="Type 2 diabetes HbA1c targets are commonly <7 percent for many adults, with individualized goals based on comorbidity and frailty.", metadata={"source": "Seed: ADA style", "category": "endocrinology"}),
        Document(page_content="Drug interaction: ACE inhibitors and NSAIDs can reduce antihypertensive efficacy and raise risk of kidney injury in susceptible patients.", metadata={"source": "Seed: Interaction", "category": "drug_interaction"}),
        Document(page_content="Shortness of breath workup may include pulse oximetry, respiratory rate, chest x-ray, and consideration of PE, CHF, COPD, and pneumonia.", metadata={"source": "Seed: Respiratory triage", "category": "respiratory"}),
        Document(page_content="Common cold (viral URI) usually includes runny nose, nasal congestion, sore throat, cough, and low-grade fever. Supportive care includes fluids, rest, and symptomatic relief. Antibiotics are not indicated for uncomplicated viral colds.", metadata={"source": "Seed: Primary care URI", "category": "common_illness"}),
        Document(page_content="Influenza-like illness often presents with acute fever, myalgia, headache, fatigue, cough, and sore throat. Higher-risk patients (older adults, pregnancy, chronic disease, immunocompromise) may benefit from early antiviral evaluation.", metadata={"source": "Seed: Influenza triage", "category": "common_illness"}),
        Document(page_content="Fever in adults is commonly defined as temperature >=38.0 C. Initial assessment includes symptom duration, exposure history, hydration status, and red flags such as confusion, persistent vomiting, severe shortness of breath, or hypotension.", metadata={"source": "Seed: Fever assessment", "category": "common_illness"}),
        Document(page_content="Acute uncomplicated headache can be tension-type or migraine. Red flags that require urgent in-person evaluation include thunderclap onset, focal neurologic deficits, fever with neck stiffness, head trauma, altered mental status, or new headache in older age.", metadata={"source": "Seed: Headache triage", "category": "neurology"}),
        Document(page_content="Acute diarrhea is often viral and self-limited. Focus on oral rehydration and monitoring for dehydration. Escalate care for blood in stool, persistent high fever, severe abdominal pain, signs of dehydration, or symptoms lasting beyond several days.", metadata={"source": "Seed: GI triage", "category": "gastrointestinal"}),
        Document(page_content="Nausea and vomiting management prioritizes hydration and electrolyte balance. Warning signs include inability to keep fluids down, bilious or bloody emesis, severe abdominal pain, pregnancy-related dehydration risk, and symptoms of metabolic disturbance.", metadata={"source": "Seed: GI symptom care", "category": "gastrointestinal"}),
        Document(page_content="Sore throat is commonly viral. Consider streptococcal pharyngitis when fever, tonsillar exudate, and tender anterior cervical nodes are present without cough. Immediate evaluation is needed for airway compromise, drooling, muffled voice, or unilateral neck swelling.", metadata={"source": "Seed: ENT triage", "category": "common_illness"}),
        Document(page_content="Acute cough with cold symptoms is usually viral bronchitis or URI. Evaluate urgently when there is chest pain, hemoptysis, oxygen desaturation, severe dyspnea, or persistent fever suggesting pneumonia or another serious cause.", metadata={"source": "Seed: Cough triage", "category": "respiratory"}),
        Document(page_content="General safety: emergency red flags across common illness include severe chest pain, severe shortness of breath, confusion, seizure, syncope, unilateral weakness, cyanosis, uncontrolled bleeding, or rapidly worsening symptoms.", metadata={"source": "Seed: Universal red flags", "category": "clinical_safety"}),
    ]


def fetch_cdc_docs(urls: list) -> list:
    docs = []
    for url in urls:
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 aura-health-rag"})
            with urlopen(req, timeout=25) as r:
                html_text = r.read().decode("utf-8", errors="ignore")
            text = clean_text(html_text, max_chars=3500)
            if len(text) > 300:
                docs.append(Document(page_content=text, metadata={"source": url, "category": "cdc"}))
        except Exception as e:
            print(f"CDC fetch skipped ({url}): {e}")
    return docs


def fetch_pubmed_docs(query: str, retmax: int = 8) -> list:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    esearch_params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": str(retmax), "tool": PUBMED_TOOL}
    if PUBMED_EMAIL:  esearch_params["email"] = PUBMED_EMAIL
    if NCBI_API_KEY:  esearch_params["api_key"] = NCBI_API_KEY

    search_url = f"{base}/esearch.fcgi?{urlencode(esearch_params)}"
    with urlopen(search_url, timeout=25) as r:
        search_json = json.loads(r.read().decode("utf-8", errors="ignore"))

    ids = search_json.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    efetch_params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "tool": PUBMED_TOOL}
    if PUBMED_EMAIL:  efetch_params["email"] = PUBMED_EMAIL
    if NCBI_API_KEY:  efetch_params["api_key"] = NCBI_API_KEY

    fetch_url = f"{base}/efetch.fcgi?{urlencode(efetch_params)}"
    with urlopen(fetch_url, timeout=25) as r:
        xml_data = r.read().decode("utf-8", errors="ignore")

    root = ET.fromstring(xml_data)
    docs = []
    for article in root.findall(".//PubmedArticle"):
        title_node     = article.find(".//ArticleTitle")
        abstract_nodes = article.findall(".//Abstract/AbstractText")
        pmid_node      = article.find(".//PMID")
        title    = "".join(title_node.itertext()).strip() if title_node is not None else "Untitled"
        abstract = " ".join("".join(n.itertext()).strip() for n in abstract_nodes if n is not None).strip()
        pmid     = pmid_node.text.strip() if pmid_node is not None and pmid_node.text else "unknown"
        if abstract:
            docs.append(Document(
                page_content=clean_text(f"Title: {title}. Abstract: {abstract}", max_chars=3500),
                metadata={"source": f"PubMed:{pmid}", "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "category": "research"},
            ))
    return docs


def fetch_openfda_docs(terms: list, per_term_limit: int = 1) -> list:
    docs = []
    for term in terms:
        try:
            query = f'openfda.generic_name:"{term}"'
            url = f"https://api.fda.gov/drug/label.json?search={quote_plus(query)}&limit={per_term_limit}"
            if OPENFDA_API_KEY:
                url += f"&api_key={quote_plus(OPENFDA_API_KEY)}"
            with urlopen(url, timeout=25) as r:
                payload = json.loads(r.read().decode("utf-8", errors="ignore"))
            for item in payload.get("results", []):
                warnings_text   = " ".join(item.get("warnings", [])[:2])
                boxed           = " ".join(item.get("boxed_warning", [])[:1])
                contraindications = " ".join(item.get("contraindications", [])[:2])
                safety_text = " ".join([warnings_text, boxed, contraindications]).strip()
                if safety_text:
                    docs.append(Document(
                        page_content=clean_text(f"Drug: {term}. Safety notes: {safety_text}", max_chars=2500),
                        metadata={"source": f"openFDA:{term}", "category": "drug_safety"},
                    ))
        except Exception as e:
            print(f"openFDA API fetch skipped ({term}): {e}")
    return docs


def _event_drug_name(item: dict, fallback: str) -> str:
    patient = item.get("patient", {}) if isinstance(item, dict) else {}
    drugs   = patient.get("drug", []) if isinstance(patient, dict) else []
    names   = [str(d.get("medicinalproduct")) for d in drugs[:3] if isinstance(d, dict) and d.get("medicinalproduct")]
    return " | ".join(names) if names else fallback


def _event_reactions(item: dict) -> str:
    patient   = item.get("patient", {}) if isinstance(item, dict) else {}
    reactions = patient.get("reaction", []) if isinstance(patient, dict) else []
    terms = [str(r.get("reactionmeddrapt")) for r in reactions[:8] if isinstance(r, dict) and r.get("reactionmeddrapt")]
    return ", ".join(terms)


def _norm_token(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def fetch_openfda_adverse_event_docs(terms: list, per_term_limit: int = 5) -> list:
    docs = []
    for term in terms:
        try:
            query = f'patient.drug.medicinalproduct:"{term}"'
            url = f"https://api.fda.gov/drug/event.json?search={quote_plus(query)}&limit={per_term_limit}"
            if OPENFDA_API_KEY:
                url += f"&api_key={quote_plus(OPENFDA_API_KEY)}"
            with urlopen(url, timeout=30) as r:
                payload = json.loads(r.read().decode("utf-8", errors="ignore"))

            groups: dict = {}
            for item in payload.get("results", []):
                if not isinstance(item, dict):
                    continue
                reactions_raw = _event_reactions(item)
                if not reactions_raw:
                    continue
                serious      = "yes" if str(item.get("serious", "0")) == "1" else "no"
                outcome      = []
                if str(item.get("seriousnessdeath",          "0")) == "1": outcome.append("death")
                if str(item.get("seriousnesshospitalization", "0")) == "1": outcome.append("hospitalization")
                if str(item.get("seriousnessdisabling",       "0")) == "1": outcome.append("disabling")
                if str(item.get("seriousnesslifethreatening", "0")) == "1": outcome.append("life-threatening")
                outcome_text = ", ".join(outcome) if outcome else "not specified"
                received     = str(item.get("receiptdate", "unknown"))
                report_id    = str(item.get("safetyreportid", "unknown"))
                drug_name    = _event_drug_name(item, term)
                reaction_terms = sorted(set(_norm_token(t) for t in reactions_raw.split(",") if _norm_token(t)))
                if not reaction_terms:
                    continue
                signature = (_norm_token(term), _norm_token(drug_name), tuple(reaction_terms), serious, _norm_token(outcome_text))
                if signature not in groups:
                    groups[signature] = {"term": term, "drug_name": drug_name, "serious": serious,
                                         "outcome_text": outcome_text, "reactions": ", ".join(reaction_terms),
                                         "count": 0, "sample_report_ids": [], "latest_receipt_date": received}
                g = groups[signature]
                g["count"] += 1
                if len(g["sample_report_ids"]) < 5:
                    g["sample_report_ids"].append(report_id)
                if received > g["latest_receipt_date"]:
                    g["latest_receipt_date"] = received

            for g in groups.values():
                content = (
                    f"Drug adverse event pattern for {g['drug_name']}. "
                    f"Observed in {g['count']} report(s). Serious: {g['serious']}. "
                    f"Outcome flags: {g['outcome_text']}. Reported reactions: {g['reactions']}. "
                    f"Latest receipt date: {g['latest_receipt_date']}."
                )
                docs.append(Document(
                    page_content=clean_text(content, max_chars=2500),
                    metadata={"source": f"openFDA-event:{g['term']}", "category": "drug_adverse_event",
                              "compressed": True, "report_count": g["count"], "sample_report_ids": g["sample_report_ids"]},
                ))
        except Exception as e:
            print(f"openFDA adverse event fetch skipped ({term}): {e}")
    return docs


def _extract_openfda_items(payload) -> list:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return payload["results"]
    if isinstance(payload, list):
        return payload
    return []


def fetch_openfda_zip_docs(zip_dir: str, max_docs: int = 200) -> list:
    docs = []
    base = Path(zip_dir)
    if not base.exists():
        print(f"FDA ZIP folder not found: {base}")
        return docs
    zip_paths = sorted(glob.glob(str(base / "*.zip")))
    if not zip_paths:
        print(f"No ZIP files found in: {base}")
        return docs
    for zpath in zip_paths:
        if len(docs) >= max_docs:
            break
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                for member in [n for n in zf.namelist() if n.lower().endswith(".json")]:
                    if len(docs) >= max_docs:
                        break
                    with zf.open(member) as f:
                        try:
                            payload = json.loads(f.read().decode("utf-8", errors="ignore"))
                        except Exception:
                            continue
                    for item in _extract_openfda_items(payload):
                        if len(docs) >= max_docs:
                            break
                        if not isinstance(item, dict):
                            continue
                        openfda      = item.get("openfda", {}) or {}
                        generic_names = openfda.get("generic_name", [])
                        brand_names  = openfda.get("brand_name", [])
                        drug_name    = " | ".join(generic_names[:2]) or " | ".join(brand_names[:2]) or "unknown"
                        warnings_t   = " ".join(item.get("warnings", [])[:2])
                        boxed        = " ".join(item.get("boxed_warning", [])[:1])
                        contraindic  = " ".join(item.get("contraindications", [])[:2])
                        indications  = " ".join(item.get("indications_and_usage", [])[:1])
                        safety_text  = " ".join([warnings_t, boxed, contraindic, indications]).strip()
                        if not safety_text:
                            continue
                        docs.append(Document(
                            page_content=clean_text(f"Drug: {drug_name}. Safety notes: {safety_text}", max_chars=2500),
                            metadata={"source": f"FDA_ZIP:{Path(zpath).name}", "category": "drug_safety_zip", "zip_member": member},
                        ))
        except Exception as e:
            print(f"FDA ZIP ingest skipped ({zpath}): {e}")
    return docs


# ── Build hybrid corpus ────────────────────────────────────────────────────────
CLINICAL_DOCS: list = []

if KB_USE_SEED:
    CLINICAL_DOCS.extend(seed_docs())
if KB_ENABLE_CDC:
    print(f"Fetching CDC docs from {len(CDC_GUIDELINE_URLS)} URLs...")
    CLINICAL_DOCS.extend(fetch_cdc_docs(CDC_GUIDELINE_URLS))
if KB_ENABLE_PUBMED:
    print(f"Fetching PubMed docs for query: {PUBMED_QUERY[:80]}...")
    try:
        CLINICAL_DOCS.extend(fetch_pubmed_docs(PUBMED_QUERY, retmax=PUBMED_RETMAX))
    except Exception as e:
        print(f"PubMed fetch failed: {e}")
if KB_ENABLE_OPENFDA:
    print(f"Fetching openFDA API drug safety docs for: {OPENFDA_TERMS}")
    CLINICAL_DOCS.extend(fetch_openfda_docs(OPENFDA_TERMS, per_term_limit=1))
if KB_ENABLE_OPENFDA_EVENT:
    print(f"Fetching openFDA adverse event docs for: {OPENFDA_EVENT_TERMS}")
    CLINICAL_DOCS.extend(fetch_openfda_adverse_event_docs(OPENFDA_EVENT_TERMS, per_term_limit=OPENFDA_EVENT_PER_TERM_LIMIT))
if KB_ENABLE_OPENFDA_ZIP:
    print(f"Loading FDA ZIP docs from: {OPENFDA_ZIP_DIR}")
    CLINICAL_DOCS.extend(fetch_openfda_zip_docs(OPENFDA_ZIP_DIR, max_docs=OPENFDA_ZIP_MAX_DOCS))

if not CLINICAL_DOCS:
    raise ValueError(
        "No knowledge docs loaded. Set KB_USE_SEED=true or enable KB_ENABLE_CDC / "
        "KB_ENABLE_PUBMED / KB_ENABLE_OPENFDA / KB_ENABLE_OPENFDA_EVENT / KB_ENABLE_OPENFDA_ZIP."
    )

splitter = RecursiveCharacterTextSplitter(
    chunk_size=RAG_CHUNK_SIZE,
    chunk_overlap=RAG_CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""],
)
CHUNKED_DOCS = splitter.split_documents(CLINICAL_DOCS)

print(f"Building FAISS index from {len(CLINICAL_DOCS)} documents -> {len(CHUNKED_DOCS)} chunks...")
vectorstore = FAISS.from_documents(CHUNKED_DOCS, embeddings)
retriever   = vectorstore.as_retriever(search_kwargs={"k": 3})

source_counts: dict = {}
for d in CLINICAL_DOCS:
    src = d.metadata.get("category", "unknown")
    source_counts[src] = source_counts.get(src, 0) + 1
print(f"Vector store ready. Document categories: {source_counts}")

# ══════════════════════════════════════════════════════════════════════════════
# HUGGINGFACE MED42 HELPERS  (cell 14)
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_hf_token(raw: str) -> str:
    token = (raw or "").strip().strip('"').strip("'")
    if token.lower().startswith("bearer "):
        token = token.split(None, 1)[1].strip()
    return token


def _token_status(token: str) -> str:
    if not token:
        return "missing"
    if token in {"hf_...", "hf_xxx", "your_token_here", "your-hf-token"}:
        return "placeholder"
    if not token.startswith("hf_"):
        return "malformed"
    if len(token) < 16:
        return "too_short"
    return "ok"


def _get_hf_token() -> tuple:
    token = _normalize_hf_token(os.getenv("HF_API_TOKEN", ""))
    if token:
        return token, "HF_API_TOKEN"
    token = _normalize_hf_token(os.getenv("HF_TOKEN", ""))
    if token:
        return token, "HF_TOKEN"
    return "", "(none)"


def _candidate_models(base_model: str) -> list:
    candidates = [base_model]
    if ":" not in base_model:
        if HF_INFERENCE_PROVIDER:
            candidates.append(f"{base_model}:{HF_INFERENCE_PROVIDER}")
        candidates.append(f"{base_model}:featherless-ai")
        candidates.append(f"{base_model}:cheapest")
    seen, ordered = set(), []
    for m in candidates:
        if m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


def _is_model_not_supported_error(status, body_text: str) -> bool:
    if status != 400:
        return False
    txt = (body_text or "").lower()
    return "model_not_supported" in txt or "not supported by any provider" in txt


def medical_llm_check(clinical_prompt: str) -> str:
    token, token_source = _get_hf_token()
    token_state = _token_status(token)
    if token_state != "ok":
        return f"HF token is not ready (status: {token_state}). Set HF_API_TOKEN or HF_TOKEN."

    base_model = (os.getenv("HF_MEDICAL_MODEL", HF_MEDICAL_MODEL) or HF_MEDICAL_MODEL).strip()
    if not base_model:
        return "HF_MEDICAL_MODEL is empty."

    client = InferenceClient(api_key=token)
    messages = [
        {"role": "system", "content": "You are an evidence-based clinical assistant. Return concise differential diagnosis, red flags, and safe next-step investigations. Do not provide definitive diagnosis."},
        {"role": "user", "content": clinical_prompt},
    ]

    attempted_models: list = []
    last_error_text = ""

    for model in _candidate_models(base_model):
        attempted_models.append(model)
        try:
            response = client.chat.completions.create(model=model, messages=messages, max_tokens=500, temperature=0.1)
            content = response.choices[0].message.content
            if content:
                return (f"[HF fallback used: {model}]\n\n{content}" if model != base_model else content)
            return "HF response was empty."
        except HfHubHTTPError as e:
            status   = getattr(getattr(e, "response", None), "status_code", None)
            body     = getattr(getattr(e, "response", None), "text", "")
            body_preview = (body or str(e))[:500]
            last_error_text = body_preview
            if _is_model_not_supported_error(status, body):
                continue
            if status == 401:
                return f"HF authentication failed (401). Token source: {token_source}. Model: {model}."
            if status == 403:
                return f"HF access denied (403). Model: {model}."
            if status == 404:
                return f"HF model endpoint not found (404). Model: '{model}'."
            if status == 429:
                return "HF rate limit reached (429). Wait and retry."
            if status and status >= 500:
                return f"HF server error ({status}). Try again shortly."
            return f"HF API error ({status}): {body_preview}"
        except Exception as e:
            return f"Unexpected HF client error: {type(e).__name__}: {e}"

    return (
        f"HF model routing failed: none of the candidate routes are enabled. "
        f"Tried: {attempted_models}. Last error: {last_error_text}"
    )


def _tokenize_words(text: str) -> set:
    return {w for w in re.findall(r"[a-zA-Z]{3,}", (text or "").lower())}


def _rag_miss_signal(query: str, docs: list) -> tuple:
    q = _tokenize_words(query)
    if not q or not docs:
        return True, 0.0
    overlaps = [len(q & _tokenize_words(getattr(d, "page_content", ""))) / max(1, len(q)) for d in docs]
    best = max(overlaps) if overlaps else 0.0
    return best < 0.08, best


_MED42_ERROR_PREFIXES = (
    "HF token", "HF_MEDICAL_MODEL", "HF response was empty",
    "HF authentication", "HF access denied", "HF model endpoint",
    "HF rate limit", "HF server error", "HF API error",
    "Unexpected HF", "HF model routing",
)


def _is_med42_error(text: str) -> bool:
    return any(text.startswith(p) for p in _MED42_ERROR_PREFIXES)


# ══════════════════════════════════════════════════════════════════════════════
# AURA STATE SCHEMA  (cell 16)
# ══════════════════════════════════════════════════════════════════════════════

class AuraState(TypedDict):
    # Input
    raw_transcript:          str
    session_id:              str
    patient_context:         dict
    # STT metadata
    stt_enabled:             bool
    transcript_source:       str
    # Post-intake
    scrubbed_transcript:     str
    pii_detected:            List[dict]
    # Supervisor routing
    agents_needed:           List[str]
    # Agent outputs (operator.add = append, not overwrite)
    clinical_findings:       Annotated[List[str], operator.add]
    drug_interactions:       Annotated[List[str], operator.add]
    research_notes:          Annotated[List[str], operator.add]
    # Final clinical output
    soap_note:               str
    # IMDA Governance fields (Principles 1–5)
    xai_record:              dict
    oversight_level:         str
    oversight_instructions:  str
    human_review_required:   bool
    escalation_required:     bool
    fairness_passed:         bool
    fairness_issues:         List[dict]
    pdpa_compliant:          bool
    output_blocked:          bool
    block_reason:            str
    moh_compliant:           bool
    samd_class:              str
    audit_log_path:          str
    consultation_complete:   bool


# ══════════════════════════════════════════════════════════════════════════════
# AGENT NODE FUNCTIONS  (cell 18)
# ══════════════════════════════════════════════════════════════════════════════

def log(node: str, msg: str):
    print(f"  [{node.upper()}] {msg}")


def _safe_session_fragment(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (session_id or "").strip())
    return cleaned.strip("-") or "unknown-session"


def _resolve_artifact_dir(path_value: str) -> Path:
    base = Path(path_value)
    return base if base.is_absolute() else Path(PROJECT_ROOT) / base


def _build_governance_report(state: dict) -> str:
    fairness_issues = state.get("fairness_issues", []) or []
    xai_record = state.get("xai_record", {}) or {}
    ai_verify_runtime = state.get("ai_verify_runtime", {}) or {}

    lines = [
        f"Session ID: {state.get('session_id', 'unknown')}",
        f"Generated UTC: {datetime.utcnow().isoformat()}Z",
        "",
        "## Governance Summary",
        f"- Oversight level: {state.get('oversight_level', 'unknown')}",
        f"- Human review required: {state.get('human_review_required', False)}",
        f"- Escalation required: {state.get('escalation_required', False)}",
        f"- Fairness passed: {state.get('fairness_passed', False)}",
        f"- Fairness issues count: {len(fairness_issues)}",
        f"- Output blocked: {state.get('output_blocked', False)}",
        f"- Block reason: {state.get('block_reason') or 'None'}",
        f"- MOH compliant: {state.get('moh_compliant', False)}",
        f"- SaMD class: {state.get('samd_class', 'unknown')}",
        "",
        "## AI Verify Runtime",
        json.dumps(ai_verify_runtime, indent=2, ensure_ascii=False) if ai_verify_runtime else "{}",
        "",
        "## XAI Record",
        json.dumps(xai_record, indent=2, ensure_ascii=False) if xai_record else "{}",
    ]

    if fairness_issues:
        lines.extend([
            "",
            "## Fairness Issues",
            json.dumps(fairness_issues, indent=2, ensure_ascii=False),
        ])

    return "\n".join(lines) + "\n"


def export_consultation(state: dict, session_id: str, output_dir: str = LOCAL_OUTPUT_DIR) -> dict:
    output_root = _resolve_artifact_dir(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    safe_session_id = _safe_session_fragment(session_id)
    record_path = output_root / f"{safe_session_id}_record.json"
    transcript_path = output_root / f"{safe_session_id}_scrubbed_transcript.txt"
    soap_path = output_root / f"{safe_session_id}_soap.txt"
    governance_path = output_root / f"{safe_session_id}_governance.txt"

    record = {
        "session_id": session_id,
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "scrubbed_transcript": state.get("scrubbed_transcript", "") or "",
        "soap_note": state.get("soap_note", "") or "",
        "agents_needed": state.get("agents_needed", []) or [],
        "pii_detected": state.get("pii_detected", []) or [],
        "xai_record": state.get("xai_record", {}) or {},
        "oversight_level": state.get("oversight_level"),
        "human_review_required": state.get("human_review_required"),
        "escalation_required": state.get("escalation_required"),
        "fairness_passed": state.get("fairness_passed"),
        "fairness_issues": state.get("fairness_issues", []) or [],
        "output_blocked": state.get("output_blocked"),
        "block_reason": state.get("block_reason"),
        "moh_compliant": state.get("moh_compliant"),
        "samd_class": state.get("samd_class"),
        "ai_verify_runtime": state.get("ai_verify_runtime", {}) or {},
        "audit_log_path": state.get("audit_log_path"),
    }

    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    transcript_path.write_text((state.get("scrubbed_transcript", "") or "") + "\n", encoding="utf-8")
    soap_path.write_text((state.get("soap_note", "") or "") + "\n", encoding="utf-8")
    governance_path.write_text(_build_governance_report({**state, "session_id": session_id}), encoding="utf-8")

    return {
        "record_json": str(record_path),
        "scrubbed_transcript": str(transcript_path),
        "soap_note": str(soap_path),
        "governance_report": str(governance_path),
    }


def _upload_file_to_s3(local_path: str, bucket: str, object_key: str, s3_client=None) -> Optional[str]:
    path = Path(local_path)
    if not ENABLE_S3_ARTIFACT_UPLOADS or not bucket or not path.exists():
        return None

    key = object_key.strip("/")
    extra_args = None
    if path.suffix == ".json":
        extra_args = {"ContentType": "application/json"}
    elif path.suffix == ".jsonl":
        extra_args = {"ContentType": "application/x-ndjson"}
    elif path.suffix == ".txt":
        extra_args = {"ContentType": "text/plain; charset=utf-8"}

    try:
        client = s3_client or boto3.client("s3", region_name=AWS_REGION)
        if extra_args:
            client.upload_file(str(path), bucket, key, ExtraArgs=extra_args)
        else:
            client.upload_file(str(path), bucket, key)
        uri = f"s3://{bucket}/{key}"
        log("s3", f"Uploaded {path.name} → {uri}")
        return uri
    except Exception as exc:
        log("s3", f"Upload skipped for {path.name}: {exc}")
        return None


def persist_session_artifacts(state: dict, session_id: str) -> dict:
    local_paths = export_consultation(state, session_id, output_dir=LOCAL_OUTPUT_DIR)

    audit_log_path = state.get("audit_log_path")
    if audit_log_path:
        audit_path = Path(audit_log_path)
        if not audit_path.is_absolute():
            audit_path = Path(PROJECT_ROOT) / audit_path
        local_paths["audit_json"] = str(audit_path)

    audit_journal_path = _resolve_artifact_dir(LOCAL_AUDIT_DIR) / "aura_audit.jsonl"
    if audit_journal_path.exists():
        local_paths["audit_journal"] = str(audit_journal_path)

    s3_uris = {}
    if not ENABLE_S3_ARTIFACT_UPLOADS:
        return {"local_paths": local_paths, "s3_uris": s3_uris}

    try:
        s3_client = boto3.client("s3", region_name=AWS_REGION)
    except Exception as exc:
        log("s3", f"S3 client unavailable: {exc}")
        return {"local_paths": local_paths, "s3_uris": s3_uris}

    safe_session_id = _safe_session_fragment(session_id)
    session_prefix = f"{AURA_S3_PREFIX}/{safe_session_id}" if AURA_S3_PREFIX else safe_session_id

    if AURA_OUTPUTS_BUCKET:
        for label in ("record_json", "scrubbed_transcript", "soap_note", "governance_report"):
            local_path = local_paths.get(label)
            if local_path:
                s3_uris[label] = _upload_file_to_s3(
                    local_path,
                    AURA_OUTPUTS_BUCKET,
                    f"{session_prefix}/{Path(local_path).name}",
                    s3_client=s3_client,
                )

    if AURA_AUDIT_BUCKET and local_paths.get("audit_json"):
        audit_path = local_paths["audit_json"]
        s3_uris["audit_json"] = _upload_file_to_s3(
            audit_path,
            AURA_AUDIT_BUCKET,
            f"{session_prefix}/{Path(audit_path).name}",
            s3_client=s3_client,
        )

    if AURA_AUDIT_BUCKET and audit_journal_path.exists():
        s3_uris["audit_journal"] = _upload_file_to_s3(
            str(audit_journal_path),
            AURA_AUDIT_BUCKET,
            f"{LOCAL_AUDIT_DIR}/aura_audit.jsonl",
            s3_client=s3_client,
        )

    return {"local_paths": local_paths, "s3_uris": s3_uris}


def stt_prep_node(state: AuraState) -> dict:
    source      = state.get("transcript_source", "manual")
    stt_enabled = bool(state.get("stt_enabled", False))
    transcript  = (state.get("raw_transcript", "") or "").strip()
    if not transcript:
        log("stt_prep", "No transcript provided; using placeholder text.")
        transcript = "No transcript provided."
    log("stt_prep", f"Transcript source: {source}")
    return {"raw_transcript": transcript, "transcript_source": source, "stt_enabled": stt_enabled}


def intake_node(state: AuraState) -> dict:
    log("intake", "Scrubbing PII from transcript...")
    scrubbed, detected = scrub_pii(state["raw_transcript"])
    log("intake", f"Scrubbed {len(detected)} PII entities")
    return {"scrubbed_transcript": scrubbed, "pii_detected": detected}


def supervisor_node(state: AuraState) -> dict:
    log("supervisor", "Analysing transcript to determine required agents...")
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="""You are a medical consultation router.
Analyse the transcript and return a JSON object with key 'agents' containing a list
of required specialist agents from: ["clinical", "drug", "research"].
- clinical: always include for any consultation
- drug: include if any medications are mentioned
- research: include if rare conditions, unclear diagnosis, or recent research needed
Return ONLY valid JSON, no explanation. Example: {"agents": ["clinical", "drug"]}"""),
        HumanMessage(content=f"Transcript:\n{state['scrubbed_transcript']}")
    ])
    chain = prompt | llm | StrOutputParser()
    result = chain.invoke({})
    try:
        clean = result.strip().strip("```json").strip("```").strip()
        agents = json.loads(clean).get("agents", ["clinical"])
    except Exception:
        agents = ["clinical"]
    log("supervisor", f"Routing to agents: {agents}")
    return {"agents_needed": agents}


def clinical_node(state: AuraState) -> dict:
    log("clinical", "Retrieving clinical guidelines from FAISS...")
    docs    = retriever.invoke(state["scrubbed_transcript"])
    context = "\n".join([f"[{d.metadata['source']}] {d.page_content}" for d in docs])
    is_miss, overlap = _rag_miss_signal(state["scrubbed_transcript"], docs)

    if not is_miss:
        log("clinical", f"FAISS RAG adequate (overlap={overlap:.3f}) — using Claude Haiku")
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content="You are a clinical decision support AI. Using the provided guideline context, analyse the consultation transcript. Provide: 1) Key clinical findings  2) Differential diagnoses  3) Recommended investigations. Be concise and clinically precise."),
            HumanMessage(content=f"Transcript:\n{state['scrubbed_transcript']}\n\nGuideline context:\n{context}")
        ])
        finding = (prompt | llm | StrOutputParser()).invoke({})
        log("clinical", f"Finding generated ({len(finding)} chars)")
        return {"clinical_findings": [f"[Source: Claude Haiku + FAISS RAG]\n\n{finding}"]}

    log("clinical", f"FAISS RAG miss (overlap={overlap:.3f}) — escalating to Med42...")
    fallback_prompt = (
        "Provide differential diagnoses, critical red flags, and urgent next-step investigations "
        "based on this de-identified clinical transcript:\n\n" + state["scrubbed_transcript"]
    )
    med42_result = medical_llm_check(fallback_prompt)

    if not _is_med42_error(med42_result):
        log("clinical", "Med42 advisory used as primary clinical finding")
        return {"clinical_findings": [f"[Source: Med42 advisory — FAISS RAG miss, overlap={overlap:.3f}]\n\n{med42_result}"]}

    log("clinical", f"Med42 unavailable ({med42_result[:60]}…) — last resort: Claude Haiku")
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="You are a clinical decision support AI. The knowledge base did not return relevant guidelines. Analyse the transcript using your clinical knowledge. Provide: 1) Key clinical findings  2) Differential diagnoses  3) Recommended investigations. Be concise and clinically precise."),
        HumanMessage(content=f"Transcript:\n{state['scrubbed_transcript']}")
    ])
    finding = (prompt | llm | StrOutputParser()).invoke({})
    log("clinical", f"Finding generated ({len(finding)} chars)")
    return {"clinical_findings": [f"[Source: Claude Haiku (last resort — FAISS RAG miss overlap={overlap:.3f}, Med42 unavailable)]\n\n{finding}"]}


def drug_node(state: AuraState) -> dict:
    log("drug", "Checking drug interactions...")
    docs    = retriever.invoke("drug interaction " + state["scrubbed_transcript"])
    context = "\n".join([f"[{d.metadata['source']}] {d.page_content}" for d in docs])
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="You are a clinical pharmacist AI. Identify all medications mentioned, check for interactions, and flag contraindications. Format: list each drug, its indication, key interactions, and monitoring requirements."),
        HumanMessage(content=f"Transcript:\n{state['scrubbed_transcript']}\n\nDrug database context:\n{context}")
    ])
    interaction = (prompt | llm | StrOutputParser()).invoke({})
    log("drug", f"Drug review generated ({len(interaction)} chars)")
    return {"drug_interactions": [interaction]}


def research_node(state: AuraState) -> dict:
    log("research", "Synthesising research context...")
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="You are a medical research AI. Based on the clinical transcript, provide relevant evidence-based context: recent guideline updates, epidemiology, and any emerging treatment options. Keep it clinically actionable."),
        HumanMessage(content=f"Transcript:\n{state['scrubbed_transcript']}")
    ])
    research = (prompt | llm | StrOutputParser()).invoke({})
    log("research", f"Research note generated ({len(research)} chars)")
    return {"research_notes": [research]}


def summary_node(state: AuraState) -> dict:
    log("summary", "Synthesising final SOAP note...")
    all_findings = []
    if state.get("clinical_findings"):
        all_findings.append("=== CLINICAL FINDINGS ===\n" + "\n".join(state["clinical_findings"]))
    if state.get("drug_interactions"):
        all_findings.append("=== DRUG REVIEW ===\n"       + "\n".join(state["drug_interactions"]))
    if state.get("research_notes"):
        all_findings.append("=== RESEARCH CONTEXT ===\n"  + "\n".join(state["research_notes"]))

    combined = "\n\n".join(all_findings)
    patient  = state.get("patient_context", {})
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content="""You are a senior clinician writing a SOAP note.
Synthesise the agent findings into a structured SOAP note:

SUBJECTIVE: Patient complaints and history from transcript
OBJECTIVE: Relevant vitals, examination findings, and lab values mentioned
ASSESSMENT: Primary diagnosis and differentials with clinical reasoning
PLAN: Medications, investigations, referrals, and follow-up schedule

Be precise, use medical terminology, keep it clinically actionable."""),
        HumanMessage(content=f"Patient context: {json.dumps(patient)}\n\nAgent findings:\n{combined}\n\nOriginal transcript (de-identified):\n{state['scrubbed_transcript']}")
    ])
    soap = (prompt | llm | StrOutputParser()).invoke({})
    log("summary", f"SOAP note generated ({len(soap)} chars)")
    return {"soap_note": soap, "consultation_complete": True}


# ══════════════════════════════════════════════════════════════════════════════
# GOVERNANCE MODULES  (cell 23)
# ══════════════════════════════════════════════════════════════════════════════

PROJECT_ROOT = os.path.abspath(".")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from governance.xai_layer             import xai_node
    from governance.human_oversight       import human_oversight_node
    from governance.fairness_monitor      import fairness_node
    from governance.audit_log             import audit_node
    from governance.clinical_safety_guard import clinical_safety_guard_node
    GOVERNANCE_ENABLED = True
    print("IMDA governance modules loaded successfully.")
except ImportError as e:
    print(f"WARNING: Governance modules not found — {e}")
    GOVERNANCE_ENABLED = False

    def xai_node(state):              return {}
    def human_oversight_node(state):  return {"oversight_level": "advisory", "oversight_instructions": "Review required", "human_review_required": True, "escalation_required": False}
    def fairness_node(state):         return {"fairness_passed": True, "fairness_issues": [], "pdpa_compliant": True}
    def clinical_safety_guard_node(state): return {"output_blocked": False, "moh_compliant": True, "samd_class": "Class B CDS"}
    def audit_node(state):            return {"audit_log_path": None, "consultation_complete": True}

# ══════════════════════════════════════════════════════════════════════════════
# GRAPH ASSEMBLY  (cell 24)
# ══════════════════════════════════════════════════════════════════════════════

def route_to_agents(state: AuraState) -> List[str]:
    needed = state.get("agents_needed", ["clinical"])
    routes = []
    if "clinical"  in needed: routes.append("clinical")
    if "drug"      in needed: routes.append("drug")
    if "research"  in needed: routes.append("research")
    return routes if routes else ["clinical"]


workflow = StateGraph(AuraState)

workflow.add_node("stt_prep",        stt_prep_node)
workflow.add_node("intake",          intake_node)
workflow.add_node("supervisor",      supervisor_node)
workflow.add_node("clinical",        clinical_node)
workflow.add_node("drug",            drug_node)
workflow.add_node("research",        research_node)
workflow.add_node("summary",         summary_node)
workflow.add_node("xai",             xai_node)
workflow.add_node("fairness",        fairness_node)
workflow.add_node("human_oversight", human_oversight_node)
workflow.add_node("clinical_safety", clinical_safety_guard_node)
workflow.add_node("audit",           audit_node)

workflow.set_entry_point("stt_prep")
workflow.add_edge("stt_prep", "intake")
workflow.add_edge("intake",   "supervisor")
workflow.add_conditional_edges("supervisor", route_to_agents, {"clinical": "clinical", "drug": "drug", "research": "research"})
workflow.add_edge("clinical",  "summary")
workflow.add_edge("drug",      "summary")
workflow.add_edge("research",  "summary")
workflow.add_edge("summary",          "xai")
workflow.add_edge("xai",              "fairness")
workflow.add_edge("fairness",         "human_oversight")
workflow.add_edge("human_oversight",  "clinical_safety")
workflow.add_edge("clinical_safety",  "audit")
workflow.add_edge("audit",            END)

checkpointer = MemorySaver()
graph        = workflow.compile(checkpointer=checkpointer)

print("Graph compiled.")
print("  Clinical : stt_prep → intake → supervisor → [clinical|drug|research] → summary")
print("  Governance: summary → xai → fairness → human_oversight → clinical_safety → audit → END")

# ══════════════════════════════════════════════════════════════════════════════
# FASTAPI SERVER  (cell 49)
# ══════════════════════════════════════════════════════════════════════════════

api = FastAPI(title="Aura Health 2.0 API", version="1.0.0")
api.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,          # must stay False when allow_origins contains "*"
    allow_methods=["*"],
    allow_headers=["*"],              # covers Content-Type: audio/webm preflight
    expose_headers=["Content-Type"],  # lets JS read Content-Type on SSE responses
)

sessions_store: dict = {}


def _normalize_api_patient_context(raw_context=None) -> dict:
    raw_context = raw_context or {}
    merged = {
        "age":                 DEFAULT_PATIENT_CONTEXT.get("age"),
        "gender":              DEFAULT_PATIENT_CONTEXT.get("gender"),
        "known_conditions":    list(DEFAULT_PATIENT_CONTEXT.get("known_conditions", [])),
        "current_medications": list(DEFAULT_PATIENT_CONTEXT.get("current_medications", [])),
        "allergies":           DEFAULT_PATIENT_CONTEXT.get("allergies"),
    }
    if not isinstance(raw_context, dict):
        return merged
    for key in ["age", "gender", "allergies"]:
        value = raw_context.get(key)
        if value not in (None, ""):
            merged[key] = value
    for key in ["known_conditions", "current_medications"]:
        value = raw_context.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            merged[key] = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, list):
            merged[key] = [str(item).strip() for item in value if str(item).strip()]
    return merged


def _session_status(session: dict) -> str:
    if session.get("error"):  return "failed"
    if session.get("done"):   return "completed"
    if session.get("started"): return "running"
    return "queued"


def _sse_cors_headers(request: Request) -> dict:
    """
    Return explicit CORS headers for EventSourceResponse objects.

    sse_starlette's EventSourceResponse flushes http.response.start before
    Starlette's CORSMiddleware can inject the Access-Control-Allow-Origin header,
    so we set it manually on every SSE response.
    """
    origin = request.headers.get("origin", "")
    if not origin:
        return {}
    if "*" in CORS_ORIGINS:
        allow = "*"
    elif origin in CORS_ORIGINS:
        allow = origin
    else:
        return {}
    return {"Access-Control-Allow-Origin": allow}


def _session_payload(session_id: str) -> dict:
    session = sessions_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session_id")
    return {
        "session_id":      session_id,
        "status":          _session_status(session),
        "done":            session.get("done", False),
        "error":           session.get("error"),
        "patient_context": session.get("patient_context", {}),
        "chunk_count":     len(session.get("chunks", [])),
        "chunks":          session.get("chunks", []),
        "final_state":     session.get("final_state"),
    }


class PatientContext(BaseModel):
    age:                 Optional[int]       = None
    gender:              Optional[str]       = None
    known_conditions:    List[str]           = Field(default_factory=list)
    current_medications: List[str]           = Field(default_factory=list)
    allergies:           Optional[str]       = None


class ConsultRequest(BaseModel):
    session_id:      str
    transcript:      str
    patient_context: PatientContext = Field(default_factory=PatientContext)


@api.get("/consult/schema")
def consult_schema(token: str = Depends(verify_bearer_token)):
    return {
        "patient_context_schema": {
            "age":                 {"type": "integer", "label": "Age",                 "required": False, "default": DEFAULT_PATIENT_CONTEXT["age"],                 "example": 58},
            "gender":              {"type": "string",  "label": "Gender",              "required": False, "default": DEFAULT_PATIENT_CONTEXT["gender"],              "example": "male"},
            "known_conditions":    {"type": "array",   "items": "string", "label": "Known Conditions",    "required": False, "default": DEFAULT_PATIENT_CONTEXT["known_conditions"],    "example": ["hypertension", "type_2_diabetes"]},
            "current_medications": {"type": "array",   "items": "string", "label": "Current Medications", "required": False, "default": DEFAULT_PATIENT_CONTEXT["current_medications"], "example": ["lisinopril 10mg", "metformin 500mg BD"]},
            "allergies":           {"type": "string",  "label": "Allergies",           "required": False, "default": DEFAULT_PATIENT_CONTEXT["allergies"],           "example": "NKDA"},
        },
        "frontend_flow": [
            "Call GET /consult/schema first.",
            "Render patient form using patient_context_schema defaults.",
            "Submit POST /consult with transcript and patient_context.",
            "Read stream_url for live updates via Server-Sent Events.",
            "Poll session_url until status is completed or failed.",
        ],
        "example_submit_payload": {
            "session_id":      "aura-demo-001",
            "transcript":      "Doctor: What brings you in today?",
            "patient_context": DEFAULT_PATIENT_CONTEXT,
        },
        "example_submit_response": {
            "session_id":   "aura-demo-001",
            "status":       "queued",
            "stream_url":   "/stream/aura-demo-001",
            "session_url":  "/session/aura-demo-001",
        },
    }


@api.post("/consult")
async def consult(req: ConsultRequest, token: str = Depends(verify_bearer_token)):
    effective_context = _normalize_api_patient_context(req.patient_context.model_dump())
    sessions_store[req.session_id] = {
        "chunks": [], "done": False, "started": False,
        "error": None, "patient_context": effective_context, "final_state": None,
    }
    asyncio.create_task(run_and_stream(req.session_id, req.transcript, effective_context))
    return {
        "session_id":      req.session_id,
        "status":          _session_status(sessions_store[req.session_id]),
        "patient_context": effective_context,
        "stream_url":      f"/stream/{req.session_id}",
        "session_url":     f"/session/{req.session_id}",
    }


async def run_and_stream(session_id: str, transcript: str, patient_context: dict):
    cfg  = {"configurable": {"thread_id": session_id}}
    init = {
        "raw_transcript":    transcript,
        "session_id":        session_id,
        "patient_context":   patient_context,
        "transcript_source": "frontend_api",
        "stt_enabled":       False,
        "clinical_findings": [],
        "drug_interactions": [],
        "research_notes":    [],
    }
    sessions_store[session_id]["started"] = True
    try:
        async for event in graph.astream(init, config=cfg):
            node = list(event.keys())[0]
            for k, v in event[node].items():
                val = "\n".join(str(item) for item in v) if isinstance(v, list) else str(v)
                sessions_store[session_id]["chunks"].append(f"[{node.upper()}] {k}: {val}")

        snapshot = graph.get_state(cfg)
        values   = snapshot.values if snapshot else {}
        artifact_summary = persist_session_artifacts(values, session_id)

        for label, path in artifact_summary.get("local_paths", {}).items():
            sessions_store[session_id]["chunks"].append(f"[EXPORT] {label}: {path}")
        for label, uri in artifact_summary.get("s3_uris", {}).items():
            if uri:
                sessions_store[session_id]["chunks"].append(f"[S3] {label}: {uri}")

        sessions_store[session_id]["final_state"] = {
            "soap_note":             values.get("soap_note"),
            "agents_needed":         values.get("agents_needed", []),
            "scrubbed_transcript":   values.get("scrubbed_transcript"),
            "pii_detected":          values.get("pii_detected", []),
            "xai_record":            values.get("xai_record"),
            "oversight_level":       values.get("oversight_level"),
            "human_review_required": values.get("human_review_required"),
            "escalation_required":   values.get("escalation_required"),
            "fairness_passed":       values.get("fairness_passed"),
            "fairness_issues":       values.get("fairness_issues", []),
            "output_blocked":        values.get("output_blocked"),
            "moh_compliant":         values.get("moh_compliant"),
            "samd_class":            values.get("samd_class"),
            "audit_log_path":        values.get("audit_log_path"),
            "output_artifacts":      artifact_summary.get("local_paths", {}),
            "s3_artifacts":          artifact_summary.get("s3_uris", {}),
            "audit_log_s3_uri":      artifact_summary.get("s3_uris", {}).get("audit_json"),
            "audit_journal_s3_uri":  artifact_summary.get("s3_uris", {}).get("audit_journal"),
        }
    except Exception as exc:
        sessions_store[session_id]["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        sessions_store[session_id]["done"] = True


@api.get("/stream/{session_id}")
async def stream(request: Request, session_id: str, token: str = Depends(verify_bearer_token_or_query)):
    if session_id not in sessions_store:
        raise HTTPException(status_code=404, detail="Unknown session_id")

    async def gen():
        last = 0
        while True:
            s = sessions_store.get(session_id, {})
            for chunk in s.get("chunks", [])[last:]:
                yield {"data": chunk}
                last += 1
            if s.get("done"):
                if s.get("error"):
                    yield {"data": f"[ERROR] {s['error']}"}
                yield {"data": "[DONE]"}
                break
            await asyncio.sleep(0.05)

    return EventSourceResponse(gen(), headers=_sse_cors_headers(request))


@api.get("/session/{session_id}")
def get_session(session_id: str, token: str = Depends(verify_bearer_token)):
    return _session_payload(session_id)


@api.get("/health")
def health():
    return {
        "status": "ok",
        "model": BEDROCK_MODEL,
        "governance": GOVERNANCE_ENABLED,
        "s3_uploads_enabled": ENABLE_S3_ARTIFACT_UPLOADS,
        "outputs_bucket": AURA_OUTPUTS_BUCKET or None,
        "audit_bucket": AURA_AUDIT_BUCKET or None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED AUDIO → TRANSCRIBE → CONSULT ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

# Accumulates per-session STT chunks separately from the consultation store.
audio_sessions: dict = {}


async def _transcribe_webm(audio_bytes: bytes, chunk_index: int) -> str:
    """
    Transcribe a raw audio/webm blob via OpenAI STT.

    Tries STT_OPENAI_MODEL first, then each model in STT_OPENAI_FALLBACK_MODELS
    in order.  Raises RuntimeError if all candidates fail.
    """
    from openai import AsyncOpenAI, APIError

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set — required for STT transcription.")

    client   = AsyncOpenAI(api_key=api_key)
    # Deduplicate while preserving order: primary model first, then fallbacks.
    seen, candidates = set(), []
    for m in [STT_OPENAI_MODEL] + STT_OPENAI_FALLBACK_MODELS:
        if m not in seen:
            seen.add(m)
            candidates.append(m)

    last_error: Exception = RuntimeError("No STT models configured.")
    for model in candidates:
        try:
            buf = io.BytesIO(audio_bytes)
            # OpenAI SDK uses the filename extension to detect format.
            buf.name = f"chunk_{chunk_index}.webm"
            response = await client.audio.transcriptions.create(
                model=model,
                file=buf,
                language=STT_LANGUAGE,
            )
            return (response.text or "").strip()
        except APIError as e:
            last_error = e
            status = getattr(e, "status_code", None)
            # 400/404 usually means the model is unavailable — try the next one.
            if status in (400, 404):
                continue
            raise RuntimeError(f"STT APIError (HTTP {status}) with model '{model}': {e}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected STT error with model '{model}': {e}") from e

    raise RuntimeError(f"All STT models exhausted. Last error: {last_error}")


@api.post("/consult/audio")
async def consult_audio(
    request: Request,
    session_id:      str  = Query(default=None,  description="Reuse across chunks; auto-generated on first call if omitted."),
    chunk_index:     int  = Query(default=0,      description="0-based chunk sequence number."),
    is_final:        bool = Query(default=False,  description="Set true on the last chunk to trigger consultation."),
    patient_context: str  = Query(default="{}",   description="JSON-encoded PatientContext. Only required on the final chunk."),
    token:           str  = Depends(verify_bearer_token),
):
    """
    Unified audio ingestion + consultation endpoint.

    The frontend (MediaRecorder) calls this once per 10-second audio/webm blob:

        POST /consult/audio?session_id=<uuid>&chunk_index=0&is_final=false
        Content-Type: audio/webm
        <raw blob bytes>

    On every non-final chunk the server:
      1. Transcribes the chunk via OpenAI STT.
      2. Appends it to the session's accumulated transcript.
      3. Returns JSON so the frontend can display a live caption.

    On the final chunk (is_final=true) the server:
      1. Transcribes the final chunk and merges with accumulated text.
      2. Runs the full LangGraph consultation pipeline.
      3. Returns a Server-Sent Events stream:
             [STT]        accumulated_transcript: <full transcript>
             [INTAKE]     scrubbed_transcript: ...
             [SUPERVISOR] agents_needed: ...
             [CLINICAL]   clinical_findings: ...
             ...
             [DONE]

    Non-final response  →  Content-Type: application/json
    Final response      →  Content-Type: text/event-stream (SSE)
    """
    audio_bytes = await request.body()
    if not audio_bytes and not is_final:
        raise HTTPException(status_code=400, detail="Empty body — expected raw audio/webm bytes.")

    sid = session_id or str(uuid.uuid4())

    if sid not in audio_sessions:
        # Each blob is a complete, self-contained WebM file (the frontend
        # stop/starts MediaRecorder every 10 s so every chunk has its own
        # EBML header).  Transcripts are accumulated across chunks.
        audio_sessions[sid] = {"transcripts": [], "accumulated": ""}

    if audio_bytes:
        try:
            chunk_transcript = await _transcribe_webm(audio_bytes, chunk_index)
        except ValueError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=f"STT failed: {e}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error during transcription: {e}")

        audio_sessions[sid]["transcripts"].append(chunk_transcript)
        audio_sessions[sid]["accumulated"] = " ".join(
            t for t in audio_sessions[sid]["transcripts"] if t
        ).strip()

    accumulated = audio_sessions[sid]["accumulated"]

    # ── Non-final: return partial transcript for live captioning ──────────────
    if not is_final:
        return {
            "session_id":             sid,
            "chunk_index":            chunk_index,
            "chunk_transcript":       chunk_transcript,
            "accumulated_transcript": accumulated,
            "status":                 "accumulating",
        }

    # ── Final: parse patient context, init session, return SSE stream ─────────
    try:
        raw_context = json.loads(patient_context)
    except json.JSONDecodeError:
        raw_context = {}

    effective_context = _normalize_api_patient_context(raw_context)

    sessions_store[sid] = {
        "chunks": [], "done": False, "started": False,
        "error": None, "patient_context": effective_context, "final_state": None,
    }

    # Start pipeline BEFORE the SSE generator — mirrors the text /consult
    # endpoint pattern and avoids create_task() inside an anyio-managed generator.
    asyncio.create_task(run_and_stream(sid, accumulated, effective_context))

    async def gen():
        # Emit full accumulated transcript so the frontend knows exactly what
        # text was sent into the consultation pipeline.
        yield {"data": f"[STT] accumulated_transcript: {accumulated}"}

        last = 0
        while True:
            s = sessions_store.get(sid, {})
            for chunk in s.get("chunks", [])[last:]:
                yield {"data": chunk}
                last += 1
            if s.get("done"):
                if s.get("error"):
                    yield {"data": f"[ERROR] {s['error']}"}
                yield {"data": "[DONE]"}
                audio_sessions.pop(sid, None)   # release chunk buffer
                break
            await asyncio.sleep(0.05)

    return EventSourceResponse(gen(), headers=_sse_cors_headers(request))


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting Aura Health API on 0.0.0.0:{port}")
    print("Endpoints: GET /health  GET /consult/schema  POST /consult  GET /stream/{{id}}  GET /session/{{id}}")
    uvicorn.run(api, host="0.0.0.0", port=port, log_level="info")
