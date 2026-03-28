# Aura Health — LangGraph Multi-Agent Clinical AI

A production-ready clinical decision support system with full **IMDA Model AI Governance
Framework** compliance, **LLMSecOps** CI/CD pipeline, and **promptfoo** LLM evaluation.

---

## Quick start

```bash
# 1. Clone and enter project
cd aura_health_project/

# 2. Install Python dependencies
pip install -r requirements.txt
python -m spacy download en_core_web_lg

# Optional frontend bridge dependencies
pip install fastapi uvicorn sse-starlette

# 3. Configure credentials (copy and fill in)
cp .env.example .env
# Edit .env with your AWS or Anthropic credentials

# 4. Open the notebook
jupyter notebook aura_health_langgraph.ipynb

# 5. Run cells top to bottom (1 → 9c → 9 → 10a optional → 10b optional → 11 → 12 → 12a → 12b → 17 optional API bridge → 18 frontend examples; then 19 static eval and 21 dynamic clinical replay)
```

---

## Project structure

```
aura-health/
│
├── aura_health_langgraph.ipynb   # Main notebook (60 cells)
│
├── governance/                   # IMDA AI Governance Framework
│   ├── xai_layer.py              # Principle 2: Explainability
│   ├── human_oversight.py        # Principle 1: Human oversight
│   ├── fairness_monitor.py       # Principle 3: Fairness / PDPA
│   ├── audit_log.py              # Principle 5: Accountability
│   └── clinical_safety_guard.py  # MOH/HSA SaMD Class B
│
├── ai_verify/
│   └── test_ai_verify.py         # AI Verify unit tests + real-session integration checks
│
├── tests/
│   └── test_llmsecops.py         # LLMSecOps security tests (Gate 4)
│
├── promptfoo/                    # LLM evaluation suite (Gate 6)
│   ├── promptfoo.config.yaml     # Root config
│   ├── clinical-eval.yaml        # SOAP quality assertions
│   ├── redteam-eval.yaml         # Adversarial injection tests
│   ├── pii-eval.yaml             # PHI leak tests
│   ├── routing-eval.yaml         # Agent dispatch correctness
│   └── providers/
│       └── aura_provider.py      # LangGraph → promptfoo bridge
│
├── .github/workflows/
│   └── llmsecops.yml             # 6-gate CI/CD pipeline
│
├── docs/
│   └── model_card.md             # IMDA model card documentation
│
├── .env.example                  # Credential template
├── .gitignore
├── .pre-commit-config.yaml       # nbstripout + gitleaks pre-commit hooks
├── requirements.txt              # Runtime dependencies
└── requirements-dev.txt          # Dev + CI dependencies
```

---

## Credentials setup

### Option A — AWS Bedrock (recommended for production)

```bash
aws configure
# AWS Access Key ID:     AKIA...
# AWS Secret Access Key: xxxxxxxx
# Default region:        us-east-1
# Output format:         json
```

Then in notebook **Cell 2**:

```python
AWS_REGION    = "us-east-1"
BEDROCK_MODEL = "anthropic.claude-haiku-4-5-20251001"
```

Enable model access: AWS Console → Bedrock → Model access → Enable Claude Haiku

### Option B — Direct Anthropic API (local dev)

In notebook **Cell 2**, uncomment:

```python
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api03-..."
```

### Option C — Hugging Face medical checker (Cell 6b)

The notebook includes an optional medical-check helper that calls Hugging Face
Inference Providers.

Set in `.env`:

```bash
# Either variable is accepted (HF_API_TOKEN is checked first)
HF_API_TOKEN=hf_...
# HF_TOKEN=hf_...

# Base model (provider route is auto-attempted if needed)
HF_MEDICAL_MODEL=m42-health/Llama3-Med42-8B
```

Cell 6b now:

- validates and normalizes token input
- uses token fallback order: `HF_API_TOKEN` then `HF_TOKEN`
- retries provider routes in this order when applicable:
  - base model
  - `:featherless-ai`
  - `:cheapest`
- returns clearer diagnostics for auth, quota/rate-limit, model-route, and server errors

---

## Running tests locally

```bash
# Install dev dependencies
pip install -r requirements-dev.txt
python -m spacy download en_core_web_lg

# Gate 4: LLMSecOps security tests (mocked — no API needed)
pytest tests/test_llmsecops.py -v

# Gate 5: IMDA AI Verify governance tests
# - Unit checks: mocked governance logic
# - Integration checks: validate latest real session artifacts when available
pytest ai_verify/test_ai_verify.py -v

# All tests together
pytest tests/ ai_verify/ -v
```

---

## Running promptfoo evaluations

```bash
# Install promptfoo
npm install -g promptfoo

# Use project-level .env (recommended)
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-proj-...   # optional for side-by-side comparison
# PROMPTFOO_API_KEY=pf-...      # required for promptfoo cloud login/upload
# The promptfoo Python provider auto-loads .env from project root.

# Login to promptfoo cloud (uses key from .env)
promptfoo auth login -k <API_KEY>

# Run individual suites
cd promptfoo/
promptfoo eval --config clinical-eval.yaml
promptfoo eval --config pii-eval.yaml
promptfoo eval --config redteam-eval.yaml
promptfoo eval --config routing-eval.yaml

# Run and upload in one command
promptfoo eval --config redteam-eval.yaml --share

# Run side-by-side red team comparison (Anthropic + OpenAI)
promptfoo eval --config redteam-compare-eval.yaml --share

# Upload latest local eval to cloud
promptfoo share

# View results in browser
promptfoo view

# After sharing, the CLI prints a cloud URL like:
# https://promptfoo.app/eval/...

# Run all suites
promptfoo eval --config promptfoo.config.yaml

# Dynamic replay (Tier B) from latest consultation export
# Notebook Cell 21 auto-generates a dynamic clinical replay config
# from latest aura_outputs/*_record.json and runs it.
# Input policy: scrubbed_transcript only (no raw transcript).

# Windows helper script (auto-load .env, login, run+upload)
powershell -ExecutionPolicy Bypass -File .\run_eval_share.ps1 -Config redteam-compare-eval.yaml -Share
```

---

## CI/CD pipeline (GitHub Actions)

| Gate                     | Tool                         | Blocks merge | API needed |
| ------------------------ | ---------------------------- | ------------ | ---------- |
| 1 — Secret scan          | TruffleHog + gitleaks        | Yes          | No         |
| 2 — Code scan            | bandit + safety              | Yes          | No         |
| 3 — Notebook hygiene     | nbstripout                   | Yes          | No         |
| 4 — LLMSecOps tests      | pytest (mocked)              | Yes          | No         |
| 5 — AI Verify governance | pytest ai_verify             | Yes          | No         |
| 6 — promptfoo evals      | promptfoo (main branch only) | Yes          | Yes        |

Add these secrets to GitHub → Settings → Secrets:

- `ANTHROPIC_API_KEY` — for Gates 4–6
- `OPENAI_API_KEY` — optional for side-by-side promptfoo comparisons
- `PROMPTFOO_API_KEY` — for Gate 6 cloud login and eval upload (`promptfoo auth login -k` + `--share`)
- `AWS_ACCESS_KEY_ID` — for Bedrock (optional)
- `AWS_SECRET_ACCESS_KEY` — for Bedrock (optional)

### Pre-commit hooks (local)

```bash
pip install pre-commit
pre-commit install
# Now runs nbstripout + gitleaks on every git commit automatically
```

---

## IMDA Governance architecture

Every consultation runs through two sequential pipelines:

```
Clinical pipeline:
  START → stt_prep → intake → supervisor → [clinical | drug | research] → summary

IMDA Governance pipeline (after summary):
  summary → xai → fairness → human_oversight → clinical_safety → audit → END
```

`clinical_node` runs an inline 3-tier fallback chain on every invocation:

| Tier | Condition                  | LLM used                                         |
| ---- | -------------------------- | ------------------------------------------------ |
| 1    | FAISS RAG overlap ≥ 0.08   | Claude Haiku + FAISS context                     |
| 2    | RAG miss → Med42 reachable | Med42 (HuggingFace `m42-health/Llama3-Med42-8B`) |
| 3    | RAG miss + Med42 error     | Claude Haiku, no RAG context (last resort)       |

Each `clinical_findings` entry carries a `[Source: ...]` tag identifying which tier fired.

Optional STT step (Cell 10a):

- Set `USE_STT_MIC = True` to capture microphone audio in chunks and transcribe using OpenAI Whisper.
- If STT is disabled or no speech is recognized, Cell 11 automatically falls back to manual `TRANSCRIPT` input.

Optional STT smoke mode (Cell 10b):

- Set `STT_SMOKE_TEST = True` to inject a sample transcript without microphone recording.
- Useful for demos and CI-like runs where audio devices are unavailable.

Each governance node adds fields to `AuraState`:

| Node                         | Principle          | Adds to state                                                      |
| ---------------------------- | ------------------ | ------------------------------------------------------------------ |
| `xai_node`                   | P2 Explainability  | `xai_record`, confidence score, evidence sources                   |
| `fairness_node`              | P3 Fairness        | `fairness_passed`, `fairness_issues`, `pdpa_compliant`             |
| `human_oversight_node`       | P1 Human oversight | `oversight_level`, `oversight_instructions`                        |
| `clinical_safety_guard_node` | MOH/HSA            | `output_blocked`, `moh_compliant`, MOH disclaimer                  |
| `audit_node`                 | P5 Accountability  | `audit_log_path`, `ai_verify_runtime`, immutable JSONL audit trail |

After a consultation run, the export cell writes four artifacts to `aura_outputs/`:

- `{session_id}_record.json`
- `{session_id}_scrubbed_transcript.txt`
- `{session_id}_soap.txt`
- `{session_id}_governance.txt`

Tier B dynamic clinical replay uses the latest `*_record.json` and requires `scrubbed_transcript` in that record.
Raw transcript is not used in this replay flow.

Runtime AI Verify behavior per Aura session:

- The audit step computes dynamic AI Verify checks from the actual session state and generated SOAP output.
- The SOAP note gets an appended "AURA AI VERIFY SESSION CHECK" summary.
- The audit JSON stores `ai_verify_runtime_summary` and `ai_verify_runtime_checks`.

## Notebook scenario demos (Cell 12a)

Cell 12a contains two runnable consultation demonstrations:

- **Scenario A** — a likely RAG-miss consultation. The graph's `clinical_node` 3-tier fallback fires automatically; the cell simply reports which tier was used via the `[Source: ...]` tag in `clinical_findings`.
- **Scenario B** — a research-triggered consultation using live PubMed docs (temporary retriever swap). If PubMed is unavailable, the cell applies its own external fallback cascade: Med42 → Claude Bedrock last resort.

---

## PHP UI integration

To connect the PHP consultation frontend (from previous sessions):

```bash
# In notebook Cell 17, set:
ENABLE_API_SERVER = True

# Then start PHP dev server in separate terminal:
cd php/
php -S localhost:8080
```

The FastAPI server starts on `:8000` and serves:

- `GET /consult/schema` — schema-first frontend contract with field metadata, defaults, and example payloads
- `POST /consult` — triggers LangGraph + governance pipeline
- `GET /stream/{session_id}` — SSE node stream back to PHP or JavaScript frontend
- `GET /session/{session_id}` — final session payload with `status`, effective `patient_context`, SOAP note, governance fields, and audit path
- `GET /health` — readiness check

### Frontend bridge quick reference

Recommended frontend flow:

1. Call `GET /consult/schema`.
2. Build the patient form from `patient_context_schema`.
3. Prefill the form with returned defaults.
4. Submit `POST /consult` with `session_id`, `transcript`, and edited `patient_context`.
5. Subscribe to `GET /stream/{session_id}` for incremental node updates.
6. Poll `GET /session/{session_id}` until `status` is `completed` or `failed`.

`patient_context` supports these fields:

- `age`
- `gender`
- `known_conditions`
- `current_medications`
- `allergies`

`GET /session/{session_id}` returns one of these status values:

- `queued`
- `running`
- `completed`
- `failed`

Example schema contract returned to the frontend:

```json
{
  "patient_context_schema": {
    "age": {
      "type": "integer",
      "label": "Age",
      "required": false,
      "default": 58,
      "example": 58
    },
    "gender": {
      "type": "string",
      "label": "Gender",
      "required": false,
      "default": "male",
      "example": "male"
    },
    "known_conditions": {
      "type": "array",
      "items": "string",
      "label": "Known Conditions",
      "required": false,
      "default": ["hypertension", "type_2_diabetes"],
      "example": ["hypertension", "type_2_diabetes"]
    },
    "current_medications": {
      "type": "array",
      "items": "string",
      "label": "Current Medications",
      "required": false,
      "default": ["lisinopril 10mg", "metformin 500mg BD"],
      "example": ["lisinopril 10mg", "metformin 500mg BD"]
    },
    "allergies": {
      "type": "string",
      "label": "Allergies",
      "required": false,
      "default": "NKDA",
      "example": "NKDA"
    }
  },
  "frontend_flow": [
    "Call GET /consult/schema first.",
    "Render patient form using patient_context_schema defaults.",
    "Submit POST /consult with transcript and patient_context.",
    "Read stream_url for live updates.",
    "Poll session_url until status is completed or failed."
  ],
  "example_submit_payload": {
    "session_id": "aura-demo-001",
    "transcript": "Doctor: What brings you in today?",
    "patient_context": {
      "age": 58,
      "gender": "male",
      "known_conditions": ["hypertension", "type_2_diabetes"],
      "current_medications": ["lisinopril 10mg", "metformin 500mg BD"],
      "allergies": "NKDA"
    }
  },
  "example_submit_response": {
    "session_id": "aura-demo-001",
    "status": "queued",
    "patient_context": {
      "age": 58,
      "gender": "male",
      "known_conditions": ["hypertension", "type_2_diabetes"],
      "current_medications": ["lisinopril 10mg", "metformin 500mg BD"],
      "allergies": "NKDA"
    },
    "stream_url": "/stream/aura-demo-001",
    "session_url": "/session/aura-demo-001"
  }
}
```

The notebook includes both PHP and JavaScript frontend examples in Cell 18.

---

## Singapore regulatory references

- [IMDA Model AI Governance Framework 2nd Edition](https://www.imda.gov.sg/resources/press-releases-factsheets-and-speeches/factsheets/2020/model-ai-governance-framework-second-edition)
- [Singapore AI Verify Toolkit](https://aiverifyfoundation.sg/)
- [Singapore PDPA](https://www.pdpc.gov.sg/Overview-of-PDPA/The-Legislation/Personal-Data-Protection-Act)
- [MOH AI in Healthcare Guidelines](https://www.moh.gov.sg)
- [HSA Software as Medical Device](https://www.hsa.gov.sg/medical-devices/samd)
