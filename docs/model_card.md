# Aura Health — AI Model Card

## IMDA Model AI Governance Framework 2nd Edition Compliance

**System name:** Aura Health Clinical Decision Support  
**Version:** 1.1.0  
**Date:** 2026-03-24  
**Data residency:** United States (AWS us-east-1)  
**Contact:** aurahealth-governance@example.com

---

## 1. Intended use

Aura Health assists **licensed Singapore-registered healthcare professionals** with:

- Clinical note documentation in SOAP format
- Drug interaction screening
- Clinical guideline retrieval and summarisation

**Not intended for:** Autonomous diagnosis, prescription decisions, or use
without oversight of a licensed doctor.

---

## 2. Model details

| Field                    | Value                                                                              |
| ------------------------ | ---------------------------------------------------------------------------------- |
| Base model               | Claude Haiku (Anthropic) via AWS Bedrock                                           |
| Clinical fallback Tier 2 | Hugging Face Inference (`m42-health/Llama3-Med42-8B`) with provider-route fallback |
| Clinical fallback Tier 3 | Claude Haiku (Bedrock), no RAG context — last resort when Med42 is unavailable     |
| Deployment region        | AWS us-east-1 (N. Virginia)                                                        |
| Data residency           | United States (AWS us-east-1)                                                      |
| Knowledge cutoff         | December 2024                                                                      |
| HSA SaMD classification  | Class B — Clinical Decision Support                                                |
| Orchestration            | LangGraph multi-agent StateGraph                                                   |
| Optional input mode      | Cell 10a microphone STT + Cell 10b smoke-test STT injection                        |

Hugging Face medical checker routing behavior:

- token source order: `HF_API_TOKEN` then `HF_TOKEN`
- provider-route retry order: base model, `:featherless-ai`, then `:cheapest`
- robust HTTP-aware error handling for 401/403/404/429/5xx and invalid route responses
- notebook scenario demo location: Cell 12a includes RAG-miss and research workflows with fallback chaining

LangGraph entry sequence:

- `stt_prep -> intake -> supervisor -> [clinical | drug | research] -> summary`
- `stt_prep` records transcript source metadata (`manual` or `stt_openai_mic`) before PII scrubbing
- `.env` can auto-enable STT mic mode via `USE_STT_MIC_DEFAULT=true`

`clinical_node` 3-tier inline fallback (runs on every consultation):

| Tier | Trigger                      | LLM used                            | Agent description                                                                                                    |
| ---- | ---------------------------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 1    | FAISS lexical overlap ≥ 0.08 | Claude Haiku + FAISS RAG context    | **Clinical (Tier 1)** — context-grounded agent; retrieves relevant guidelines from FAISS and grounds Claude Haiku inference on local clinical evidence |
| 2    | RAG miss                     | Med42 via HuggingFace Inference API | **Clinical (Tier 2)** — medical LLM fallback; queries Med42 (Llama 3 clinical fine-tune) directly when local RAG context is insufficient             |
| 3    | RAG miss + Med42 unavailable | Claude Haiku, no RAG context        | **Clinical (Tier 3)** — last-resort agent; Claude Haiku with no knowledge-base context; fires only when both Tier 1 and Tier 2 are unavailable        |

Each `clinical_findings` entry carries a `[Source: ...]` tag recording which tier fired. The shared helpers `_rag_miss_signal` and `_is_med42_error` are defined in Cell 6b alongside the Med42 utilities.

---

## 3. IMDA Model AI Governance Framework alignment

| Principle                   | Status    | Implementation file              |
| --------------------------- | --------- | -------------------------------- |
| P1 — Human oversight        | Compliant | `governance/human_oversight.py`  |
| P2 — Explainability         | Compliant | `governance/xai_layer.py`        |
| P3 — Fairness               | Compliant | `governance/fairness_monitor.py` |
| P4 — Data governance / PDPA | Compliant | presidio PII scrub + audit log   |
| P5 — Accountability         | Compliant | `governance/audit_log.py`        |
| P6 — Robustness             | Compliant | promptfoo + pytest LLMSecOps     |
| P7 — Transparency           | Compliant | MOH disclaimer on every output   |

---

## 4. AI Verify toolkit test coverage

Two layers are used:

- Unit tests with controlled fixtures for deterministic governance logic checks.
- Integration tests that load the latest real Aura session artifacts from `audit_logs/` and `aura_outputs/`.

| AI Verify Principle | Test class           | Pass threshold |
| ------------------- | -------------------- | -------------- |
| Accountability      | `TestAccountability` | 100%           |
| Explainability      | `TestExplainability` | 100%           |
| Fairness            | `TestFairness`       | 100%           |
| Data governance     | `TestDataGovernance` | 100%           |
| Human oversight     | `TestHumanOversight` | 100%           |
| Robustness          | `TestRobustness`     | 100%           |
| Transparency        | `TestTransparency`   | 100%           |

Run: `pytest ai_verify/test_ai_verify.py -v`

---

## 5. Known limitations

- Not validated for paediatric, obstetric, or psychiatric cases
- Drug interaction list is not exhaustive — always cross-check with BNF/MIMS
- Knowledge base has a fixed cutoff date (December 2024)
- Performance may vary for clinical language mixing English and local terms
- Confidence score is a structural heuristic, not a clinical validation metric
- STT quality depends on microphone quality, ambient noise, and speaking pace
- Smoke-test transcript mode is synthetic and should not be used as a proxy for real audio quality

---

## 6. Data handling & PDPA compliance

- All 18 HIPAA Safe Harbor identifiers removed before LLM processing (presidio)
- No patient data stored in model weights
- Consultation audit logs retained for 7 years per MOH record-keeping requirement
- Data residency is configured for AWS us-east-1
- SHA-256 content hashes protect audit log integrity

---

## 7. Human oversight protocol

| Confidence score | Oversight level | Action required                          |
| ---------------- | --------------- | ---------------------------------------- |
| < 40%            | Blocked         | Output blocked — manual note required    |
| 40–65%           | Mandatory       | Countersignature required before any use |
| 65–80%           | Mandatory       | Doctor review and approval required      |
| > 80%            | Advisory        | Review recommended before clinical use   |

Any output containing clinical red flags (troponin, sepsis, STEMI, anaphylaxis,
overdose) triggers mandatory escalation **regardless of confidence score**.

---

## 8. CI/CD security gates

| Gate                     | Tool                        | Blocks merge |
| ------------------------ | --------------------------- | ------------ |
| 1 — Secret scan          | TruffleHog + gitleaks       | Yes          |
| 2 — Code scan            | bandit + safety             | Yes          |
| 3 — Notebook hygiene     | nbstripout                  | Yes          |
| 4 — LLMSecOps tests      | pytest (mocked)             | Yes          |
| 5 — AI Verify governance | pytest ai_verify            | Yes          |
| 6 — promptfoo evals      | promptfoo (live, main only) | Yes          |

---

## 9. Adverse event reporting

Report issues to: **aurahealth-safety@example.com**  
For PDPA queries: **aurahealth-pdpa@example.com**  
HSA medical device incident reporting: https://www.hsa.gov.sg

---

## 10. Session governance artifacts

Each consultation export now produces:

- `aura_outputs/{session_id}_record.json` (clinical + governance fields)
- `aura_outputs/{session_id}_scrubbed_transcript.txt` (session-scoped de-identified transcript)
- `aura_outputs/{session_id}_soap.txt` (SOAP note)
- `aura_outputs/{session_id}_governance.txt` (human-readable governance report)

Each session audit file also includes runtime governance verification fields:

- `ai_verify_runtime_summary` (overall pass/review and counts)
- `ai_verify_runtime_checks` (principle-by-principle dynamic checks)

Each session SOAP output includes an appended runtime section:

- `AURA AI VERIFY SESSION CHECK`

The governance text report summarizes key controls, including:

- P1 Human oversight decision
- P2 Explainability evidence and confidence
- P3 Fairness and PDPA checks
- P5 Accountability and audit-log traceability

---

## 11. Scenario-based operational demos

The notebook includes an explicit scenario demo block at Cell 12a:

- **Scenario A** — a likely RAG-miss consultation. `clinical_node` fires its 3-tier internal fallback automatically (FAISS RAG → Med42 → Claude last resort); the demo cell reports which tier was used via the `[Source: ...]` tag embedded in `clinical_findings`.
- **Scenario B** — research-triggered consultation with a live PubMed temporary retriever swap. If PubMed is unavailable, the demo applies its own external fallback cascade: Med42 → Claude Bedrock last resort.

Optional microphone capture demo:

- Cell 10a can capture live microphone audio in chunks and transcribe via OpenAI Whisper before Cell 11.
- Cell 10b can inject a sample STT transcript to exercise the same path without microphone hardware.
- Cell 21 runs Tier B dynamic clinical promptfoo replay from the latest `aura_outputs/*_record.json`.
- Tier B replay input policy is `scrubbed_transcript` only (no raw transcript replay).

These demos are for workflow validation and governance transparency, not autonomous decision-making.
