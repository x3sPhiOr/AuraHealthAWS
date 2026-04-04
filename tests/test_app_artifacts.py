import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class DummyS3Client:
    def __init__(self):
        self.calls = []

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.calls.append((Path(filename).name, bucket, key, ExtraArgs))


def test_persist_session_artifacts_exports_local_files_and_s3_uris(tmp_path):
    try:
        import app
    except ModuleNotFoundError as exc:
        pytest.skip(f"Optional runtime dependency missing for app import: {exc}")

    session_id = "aura-unit-123"
    audit_dir = tmp_path / "audit_logs"
    audit_dir.mkdir()

    session_audit_path = audit_dir / f"{session_id}_audit.json"
    session_audit_path.write_text('{"session_id": "aura-unit-123"}', encoding="utf-8")

    journal_path = audit_dir / "aura_audit.jsonl"
    journal_path.write_text('{"session_id": "aura-unit-123"}\n', encoding="utf-8")

    state = {
        "session_id": session_id,
        "scrubbed_transcript": "Patient reports chest tightness for two days.",
        "soap_note": "SUBJECTIVE: chest tightness\nOBJECTIVE: BP 150/90\nASSESSMENT: possible ACS\nPLAN: ECG and troponin",
        "agents_needed": ["clinical", "drug"],
        "pii_detected": [{"type": "PERSON", "score": 0.99}],
        "xai_record": {"model_id": "anthropic.test", "knowledge_cutoff": "2024-12-31"},
        "oversight_level": "mandatory",
        "human_review_required": True,
        "escalation_required": False,
        "fairness_passed": True,
        "fairness_issues": [],
        "output_blocked": False,
        "moh_compliant": True,
        "samd_class": "Class B CDS",
        "ai_verify_runtime": {"passed": True, "principles_passed": 9, "principles_total": 9, "principles": {}},
        "audit_log_path": str(session_audit_path),
    }

    fake_s3 = DummyS3Client()

    with (
        patch.object(app, "PROJECT_ROOT", str(tmp_path)),
        patch.object(app, "AURA_OUTPUTS_BUCKET", "out-bucket", create=True),
        patch.object(app, "AURA_AUDIT_BUCKET", "audit-bucket", create=True),
        patch.object(app, "AURA_S3_PREFIX", "sessions", create=True),
        patch.object(app, "ENABLE_S3_ARTIFACT_UPLOADS", True, create=True),
        patch("app.boto3.client", return_value=fake_s3),
    ):
        summary = app.persist_session_artifacts(state, session_id)

    assert Path(summary["local_paths"]["record_json"]).exists()
    assert Path(summary["local_paths"]["scrubbed_transcript"]).exists()
    assert Path(summary["local_paths"]["soap_note"]).exists()
    assert Path(summary["local_paths"]["governance_report"]).exists()

    assert summary["s3_uris"]["record_json"] == f"s3://out-bucket/sessions/{session_id}/{session_id}_record.json"
    assert summary["s3_uris"]["audit_json"] == f"s3://audit-bucket/sessions/{session_id}/{session_id}_audit.json"
    assert summary["s3_uris"]["audit_journal"] == "s3://audit-bucket/audit_logs/aura_audit.jsonl"

    uploaded_targets = {(bucket, key) for _, bucket, key, _ in fake_s3.calls}
    assert ("out-bucket", f"sessions/{session_id}/{session_id}_record.json") in uploaded_targets
    assert ("audit-bucket", f"sessions/{session_id}/{session_id}_audit.json") in uploaded_targets
    assert ("audit-bucket", "audit_logs/aura_audit.jsonl") in uploaded_targets
