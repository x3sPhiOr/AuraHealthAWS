# Aura Health API — Endpoint Reference

Base URL (App Runner): `https://<service-id>.<region>.awsapprunner.com`
Local dev: `http://localhost:8000`

All endpoints accept and return `application/json` unless noted otherwise.
The two consultation endpoints return `text/event-stream` (Server-Sent Events) when a session reaches the pipeline stage.

---

## Table of Contents

1. [Health Check](#1-health-check)
2. [Schema Discovery](#2-schema-discovery)
3. [Text Consultation](#3-text-consultation)
   - [Submit](#31-submit-a-consultation)
   - [Live stream](#32-live-event-stream)
   - [Session status & final state](#33-session-status--final-state)
4. [Audio Consultation (unified)](#4-audio-consultation-unified)
5. [SSE Event Reference](#5-sse-event-reference)
6. [Error Responses](#6-error-responses)
7. [Client Sequence Diagrams](#7-client-sequence-diagrams)
   - [Text flow](#71-text-transcript-flow)
   - [Audio flow](#72-audio-webm-flow-mediarecorder)

---

## 1. Health Check

```
GET /health
```

Returns the server status and active configuration. App Runner polls this endpoint to determine instance health. The endpoint only responds once the LLM smoke test at startup has passed, so a `200` response confirms Bedrock connectivity.

**Response `200 OK`**

```json
{
  "status": "ok",
  "model": "anthropic.claude-haiku-4-5-20251001-v1:0",
  "governance": true
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | Always `"ok"` when healthy. |
| `model` | `string` | Active Bedrock model ID. |
| `governance` | `boolean` | Whether IMDA governance modules loaded successfully. |

---

## 2. Schema Discovery

```
GET /consult/schema
```

Returns the canonical `PatientContext` field list with types, labels, defaults, and examples. Frontend should call this once on load and use it to render the patient intake form dynamically, so the form stays in sync with the backend without code changes.

**Response `200 OK`**

```json
{
  "patient_context_schema": {
    "age":                 { "type": "integer", "label": "Age",                 "required": false, "default": 58,                                  "example": 58 },
    "gender":              { "type": "string",  "label": "Gender",              "required": false, "default": "male",                              "example": "male" },
    "known_conditions":    { "type": "array",   "label": "Known Conditions",    "required": false, "default": ["hypertension","type_2_diabetes"],   "example": ["hypertension","type_2_diabetes"] },
    "current_medications": { "type": "array",   "label": "Current Medications", "required": false, "default": ["lisinopril 10mg","metformin 500mg BD"], "example": ["lisinopril 10mg","metformin 500mg BD"] },
    "allergies":           { "type": "string",  "label": "Allergies",           "required": false, "default": "NKDA",                              "example": "NKDA" }
  },
  "frontend_flow": [ "..." ],
  "example_submit_payload": { "..." },
  "example_submit_response": { "..." }
}
```

---

## 3. Text Consultation

Use this flow when the transcript is already available as text (typed, pasted, or produced by a client-side STT engine).

### 3.1 Submit a Consultation

```
POST /consult
Content-Type: application/json
```

Enqueues a consultation and immediately starts the LangGraph pipeline in the background. Returns a `queued` response with the URLs to follow for live events and the final result. Does **not** block — the client should open the stream URL concurrently.

**Request body**

```json
{
  "session_id": "aura-20240328-001",
  "transcript": "Doctor: What brings you in today?\nPatient: I have chest tightness and shortness of breath...",
  "patient_context": {
    "age": 58,
    "gender": "male",
    "known_conditions": ["hypertension", "type_2_diabetes"],
    "current_medications": ["lisinopril 10mg", "metformin 500mg BD"],
    "allergies": "NKDA"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | `string` | Yes | Client-generated unique identifier. Reuse the same ID for multi-turn follow-ups. |
| `transcript` | `string` | Yes | Raw consultation text. May be partial or grammatically incomplete — the graph handles it. |
| `patient_context` | `object` | No | All fields optional; any omitted field defaults to the values in `/consult/schema`. |

**Response `200 OK`**

```json
{
  "session_id":      "aura-20240328-001",
  "status":          "queued",
  "patient_context": { "age": 58, "gender": "male", "..." },
  "stream_url":      "/stream/aura-20240328-001",
  "session_url":     "/session/aura-20240328-001"
}
```

The pipeline status transitions: `queued` → `running` → `completed` | `failed`.

---

### 3.2 Live Event Stream

```
GET /stream/{session_id}
Accept: text/event-stream
```

Opens a persistent SSE connection that emits one event per graph node as the pipeline runs. The connection closes automatically with a `[DONE]` event on completion or a `[ERROR]` event followed by `[DONE]` on failure.

> Open this connection **immediately after** receiving the `POST /consult` response, ideally concurrently. Events may already be queued by the time the client connects — they are replayed from the beginning, so no events are lost.

**Events (in emission order)**

```
data: [STT_PREP] raw_transcript: Patient reports chest tightness...
data: [INTAKE] scrubbed_transcript: [PATIENT] reports chest tightness...
data: [INTAKE] pii_detected: [{"type": "PERSON", "score": 0.85}]
data: [SUPERVISOR] agents_needed: ['clinical', 'drug']
data: [CLINICAL] clinical_findings: [Source: Claude Haiku + FAISS RAG]\n\n1) Key findings...
data: [DRUG] drug_interactions: Lisinopril: ACE inhibitor...
data: [SUMMARY] soap_note: SUBJECTIVE: ...
data: [XAI] xai_record: {...}
data: [FAIRNESS] fairness_passed: True
data: [HUMAN_OVERSIGHT] oversight_level: advisory
data: [CLINICAL_SAFETY] moh_compliant: True
data: [AUDIT] audit_log_path: audit_logs/aura-20240328-001.json
data: [DONE]
```

See [Section 5](#5-sse-event-reference) for the full event taxonomy.

**Error case**

```
data: [ERROR] ValueError: Bedrock throttled — retry after backoff
data: [DONE]
```

---

### 3.3 Session Status & Final State

```
GET /session/{session_id}
```

Polling alternative for clients that cannot use SSE. Returns the accumulated event log and — once the pipeline completes — the full structured final state including the SOAP note and all governance fields.

**Response `200 OK` (in progress)**

```json
{
  "session_id":  "aura-20240328-001",
  "status":      "running",
  "done":        false,
  "error":       null,
  "chunk_count": 4,
  "chunks": [
    "[STT_PREP] raw_transcript: ...",
    "[INTAKE] scrubbed_transcript: ...",
    "..."
  ],
  "final_state": null
}
```

**Response `200 OK` (completed)**

```json
{
  "session_id":  "aura-20240328-001",
  "status":      "completed",
  "done":        true,
  "error":       null,
  "chunk_count": 12,
  "chunks":      [ "..." ],
  "final_state": {
    "soap_note":             "SUBJECTIVE: ...\nOBJECTIVE: ...\nASSESSMENT: ...\nPLAN: ...",
    "agents_needed":         ["clinical", "drug"],
    "scrubbed_transcript":   "[PATIENT] reports chest tightness...",
    "pii_detected":          [{"type": "PERSON", "score": 0.85}],
    "xai_record":            { "..." },
    "oversight_level":       "advisory",
    "human_review_required": true,
    "escalation_required":   false,
    "fairness_passed":       true,
    "fairness_issues":       [],
    "output_blocked":        false,
    "moh_compliant":         true,
    "samd_class":            "Class B CDS",
    "audit_log_path":        "audit_logs/aura-20240328-001.json"
  }
}
```

**`status` values**

| Value | Meaning |
|-------|---------|
| `queued` | Session registered, pipeline not yet started. |
| `running` | Graph is executing. |
| `completed` | All nodes finished. `final_state` is populated. |
| `failed` | An exception occurred. `error` contains the message. |

---

## 4. Audio Consultation (Unified)

```
POST /consult/audio
Content-Type: audio/webm
```

Handles raw `audio/webm` blobs produced by the browser's `MediaRecorder` API. Each call processes one 10-second chunk: it transcribes the audio, appends the text to the session's running transcript, and returns a live caption response. When the final chunk arrives (`is_final=true`), the server merges all partial transcripts and starts the full LangGraph consultation pipeline, returning an SSE stream identical in format to `GET /stream/{session_id}`.

**Query parameters**

| Parameter | Type | Default | Required | Description |
|-----------|------|---------|----------|-------------|
| `session_id` | `string` | auto-generated | No (first call) / Yes (subsequent) | Identifies the recording session. The server generates a UUID on the first call if omitted; the client must reuse it for all subsequent chunks. |
| `chunk_index` | `integer` | `0` | No | 0-based sequence number. Used to name the temporary audio buffer for the OpenAI STT API. |
| `is_final` | `boolean` | `false` | No | Set to `true` on the last chunk. Triggers transcript accumulation → consultation pipeline → SSE stream. |
| `patient_context` | `string` | `{}` | No | URL-encoded JSON string of `PatientContext`. Only meaningful on the final chunk. |

**Request body**

Raw binary `audio/webm` bytes. No multipart wrapper. The OpenAI Whisper API natively accepts `audio/webm`.

---

**Non-final chunk response `200 OK` (`application/json`)**

```json
{
  "session_id":             "aura-audio-abc123",
  "chunk_index":            1,
  "chunk_transcript":       "blood pressure has been elevated recently",
  "accumulated_transcript": "Patient says my blood pressure has been elevated recently",
  "status":                 "accumulating"
}
```

The frontend should display `accumulated_transcript` as a live caption.

---

**Final chunk response `200 OK` (`text/event-stream`)**

The response `Content-Type` switches to `text/event-stream`. The first event is always the full STT transcript; subsequent events mirror the LangGraph pipeline exactly as in `GET /stream/{session_id}`.

```
data: [STT] accumulated_transcript: Patient says my blood pressure has been elevated recently and I have been taking lisinopril
data: [STT_PREP] raw_transcript: Patient says my blood pressure...
data: [INTAKE] scrubbed_transcript: [PATIENT] says blood pressure has been elevated...
data: [SUPERVISOR] agents_needed: ['clinical', 'drug']
data: [CLINICAL] clinical_findings: ...
data: [DRUG] drug_interactions: ...
data: [SUMMARY] soap_note: SUBJECTIVE: ...
data: [XAI] xai_record: ...
data: [FAIRNESS] fairness_passed: True
data: [HUMAN_OVERSIGHT] oversight_level: advisory
data: [CLINICAL_SAFETY] moh_compliant: True
data: [AUDIT] audit_log_path: audit_logs/aura-audio-abc123.json
data: [DONE]
```

**STT model fallback chain**

The server tries models in this order, falling back on `400`/`404`:

1. `STT_OPENAI_MODEL` env var (default: `gpt-4o-mini-transcribe`)
2. Models in `STT_OPENAI_FALLBACK_MODELS` env var (default: `gpt-4o-mini-transcribe,whisper-1`)

**Error responses**

| Status | Condition |
|--------|-----------|
| `400` | Request body is empty. |
| `502` | All STT models failed (OpenAI API error). |
| `503` | `OPENAI_API_KEY` is not set. |

---

## 5. SSE Event Reference

All SSE responses use the format:

```
data: [NODE_NAME] field_name: value\n\n
```

Events are emitted in LangGraph node execution order. The governance layer always runs after `[SUMMARY]`.

| Event prefix | Source node | Key fields emitted |
|---|---|---|
| `[STT]` | Audio endpoint only | `accumulated_transcript` |
| `[STT_PREP]` | `stt_prep_node` | `raw_transcript`, `transcript_source`, `stt_enabled` |
| `[INTAKE]` | `intake_node` | `scrubbed_transcript`, `pii_detected` |
| `[SUPERVISOR]` | `supervisor_node` | `agents_needed` |
| `[CLINICAL]` | `clinical_node` | `clinical_findings` — includes source tier: `Claude Haiku + FAISS RAG` / `Med42 advisory` / `Claude Haiku (last resort)` |
| `[DRUG]` | `drug_node` | `drug_interactions` |
| `[RESEARCH]` | `research_node` | `research_notes` |
| `[SUMMARY]` | `summary_node` | `soap_note`, `consultation_complete` |
| `[XAI]` | `xai_node` | `xai_record` |
| `[FAIRNESS]` | `fairness_node` | `fairness_passed`, `fairness_issues`, `pdpa_compliant` |
| `[HUMAN_OVERSIGHT]` | `human_oversight_node` | `oversight_level`, `oversight_instructions`, `human_review_required`, `escalation_required` |
| `[CLINICAL_SAFETY]` | `clinical_safety_guard_node` | `output_blocked`, `block_reason`, `moh_compliant`, `samd_class` |
| `[AUDIT]` | `audit_node` | `audit_log_path`, `consultation_complete` |
| `[ERROR]` | Server | Error message string. Always followed by `[DONE]`. |
| `[DONE]` | Server | Terminal event. No field value. Close the EventSource on receipt. |

> `[CLINICAL]`, `[DRUG]`, and `[RESEARCH]` are conditionally emitted. The supervisor routes to a subset of these based on the transcript content. `[CLINICAL]` is always included.

**`oversight_level` values**

| Value | Meaning |
|-------|---------|
| `advisory` | Output can be shown; human review recommended. |
| `mandatory` | Human must review before acting on the output. |
| `escalate` | Immediate senior clinician review required. |

---

## 6. Error Responses

All error responses follow FastAPI's standard format:

```json
{
  "detail": "Human-readable error message"
}
```

| Status | Endpoint | Cause |
|--------|----------|-------|
| `404` | `GET /stream/{id}`, `GET /session/{id}` | `session_id` was never registered via `POST /consult`. |
| `400` | `POST /consult/audio` | Empty request body. |
| `502` | `POST /consult/audio` | All OpenAI STT models failed. |
| `503` | `POST /consult/audio` | `OPENAI_API_KEY` environment variable not set. |

Pipeline errors (Bedrock unavailable, graph exception) are surfaced as `[ERROR]` SSE events inside an open stream, not as HTTP error codes, because the HTTP response has already been sent with `200` by the time the error occurs.

---

## 7. Client Sequence Diagrams

### 7.1 Text Transcript Flow

Use when transcript text is already available (typed, copy-pasted, or transcribed client-side).

```
Client                                    Server
  │                                          │
  │── GET /consult/schema ──────────────────>│
  │<─ 200 { patient_context_schema, ... } ───│  Render patient intake form
  │                                          │
  │── POST /consult ────────────────────────>│
  │   { session_id, transcript,              │  Graph pipeline starts in background
  │     patient_context }                    │
  │<─ 200 { session_id, status:"queued",     │
  │         stream_url, session_url } ───────│
  │                                          │
  │── GET /stream/{session_id} ─────────────>│  Open EventSource immediately
  │                                          │
  │<── data: [STT_PREP] raw_transcript: ... ─│
  │<── data: [INTAKE] scrubbed_transcript: ──│  Display live caption / progress
  │<── data: [SUPERVISOR] agents_needed: ────│
  │<── data: [CLINICAL] clinical_findings: ──│  Display partial findings
  │<── data: [DRUG] drug_interactions: ──────│
  │<── data: [SUMMARY] soap_note: ───────────│  Display SOAP note
  │<── data: [XAI] xai_record: ─────────────│
  │<── data: [FAIRNESS] fairness_passed: ────│
  │<── data: [HUMAN_OVERSIGHT] ... ──────────│
  │<── data: [CLINICAL_SAFETY] ... ──────────│
  │<── data: [AUDIT] audit_log_path: ────────│
  │<── data: [DONE] ─────────────────────────│  Close EventSource
  │                                          │
  │── GET /session/{session_id} ────────────>│  Optional: fetch structured final state
  │<─ 200 { final_state: { soap_note, ... } }│
```

---

### 7.2 Audio/WebM Flow (MediaRecorder)

Use when recording directly from the browser microphone. The frontend accumulates 10-second `audio/webm` blobs via `MediaRecorder.ondataavailable`.

```
Client (MediaRecorder)                    Server
  │                                          │
  │── GET /consult/schema ──────────────────>│
  │<─ 200 { patient_context_schema, ... } ───│  Render patient form
  │                                          │
  │  mediaRecorder.start(10_000)             │  Begin 10-second chunked recording
  │                                          │
  │  [chunk 0 ready — t=10s]                 │
  │── POST /consult/audio ──────────────────>│
  │   ?session_id=<uuid>                     │  Server auto-generates session_id
  │   &chunk_index=0&is_final=false          │  if omitted on first call
  │   Content-Type: audio/webm               │
  │   <10s blob>                             │
  │<─ 200 { session_id, chunk_transcript,    │
  │         accumulated_transcript,          │  Display live caption
  │         status:"accumulating" } ─────────│
  │                                          │
  │  [chunk 1 ready — t=20s]                 │
  │── POST /consult/audio ──────────────────>│
  │   ?session_id=<uuid>                     │  Reuse session_id from chunk 0
  │   &chunk_index=1&is_final=false          │
  │   Content-Type: audio/webm               │
  │   <10s blob>                             │
  │<─ 200 { accumulated_transcript: "..." }──│  Caption grows
  │                                          │
  │  [user stops recording — mediaRecorder.stop()]
  │                                          │
  │  [final chunk ready]                     │
  │── POST /consult/audio ──────────────────>│
  │   ?session_id=<uuid>                     │
  │   &chunk_index=2&is_final=true           │
  │   &patient_context=%7B%22age%22%3A58...%7D
  │   Content-Type: audio/webm               │
  │   <final blob>                           │
  │                                          │  Server merges all chunk transcripts
  │                                          │  and starts LangGraph pipeline
  │<── text/event-stream ────────────────────│
  │<── data: [STT] accumulated_transcript: ──│  Full transcript confirmed
  │<── data: [STT_PREP] raw_transcript: ... ─│
  │<── data: [INTAKE] scrubbed_transcript: ──│
  │<── data: [SUPERVISOR] agents_needed: ────│
  │<── data: [CLINICAL] clinical_findings: ──│
  │<── data: [DRUG] drug_interactions: ──────│
  │<── data: [SUMMARY] soap_note: ───────────│
  │<── data: [XAI] ... ──────────────────────│
  │<── data: [FAIRNESS] ... ─────────────────│
  │<── data: [HUMAN_OVERSIGHT] ... ──────────│
  │<── data: [CLINICAL_SAFETY] ... ──────────│
  │<── data: [AUDIT] audit_log_path: ────────│
  │<── data: [DONE] ─────────────────────────│  Close EventSource
```

> **Note on silence / incomplete sentences:** The STT engine transcribes whatever audio is present in each blob. If a chunk contains silence, the transcript for that chunk will be empty and is silently skipped during accumulation. The pipeline handles an incomplete or grammatically partial accumulated transcript without error — the supervisor will still route to `clinical` and the graph will run to completion.

---

## Environment Variables Affecting API Behaviour

| Variable | Default | Effect |
|----------|---------|--------|
| `BEDROCK_MODEL` | `anthropic.claude-haiku-4-5-20251001-v1:0` | LLM used by all graph nodes. |
| `BEDROCK_INFERENCE_PROFILE_ID` | _(empty)_ | If set, overrides model ID with an inference profile ARN. |
| `ANTHROPIC_API_KEY` | _(empty)_ | Fallback LLM if Bedrock is unreachable. |
| `OPENAI_API_KEY` | _(empty)_ | Required for `POST /consult/audio`. |
| `STT_OPENAI_MODEL` | `gpt-4o-mini-transcribe` | Primary STT model. |
| `STT_OPENAI_FALLBACK_MODELS` | `gpt-4o-mini-transcribe,whisper-1` | Comma-separated fallback STT models. |
| `STT_LANGUAGE` | `en` | BCP-47 language hint passed to Whisper. |
| `HF_API_TOKEN` | _(empty)_ | Enables the Med42 second-opinion tier in `clinical_node`. |
| `KB_USE_SEED` | `true` | Loads the built-in 8-document clinical seed corpus. |
| `KB_ENABLE_PUBMED` | `false` | Fetches PubMed abstracts into FAISS at startup. |
| `KB_ENABLE_CDC` | `false` | Fetches CDC guideline pages into FAISS at startup. |
| `KB_ENABLE_OPENFDA` | `false` | Fetches openFDA drug labels into FAISS at startup. |
