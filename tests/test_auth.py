"""
tests/test_auth.py
==================
Authentication & authorization tests for Bearer token validation.
Tests that all protected endpoints require valid API keys and /health is public.
"""

import pytest
import os
import sys
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def mock_env_with_keys():
    """Mock environment with test API keys."""
    with patch.dict(os.environ, {"API_KEYS": "test_key_1,test_key_2,test_key_3"}):
        # Force reimport to pick up new env vars
        import importlib
        import app_auth
        importlib.reload(app_auth)
        yield
        importlib.reload(app_auth)


@pytest.fixture
def mock_env_no_keys():
    """Mock environment without API keys configured."""
    with patch.dict(os.environ, {"API_KEYS": ""}, clear=False):
        import importlib
        import app_auth
        importlib.reload(app_auth)
        yield
        importlib.reload(app_auth)


@pytest.fixture
def client():
    """FastAPI test client."""
    from fastapi.testclient import TestClient
    from app import api
    return TestClient(api)


# ─────────────────────────────────────────────────────────────────────────────
# Test: /health endpoint is public (no auth required)
# ─────────────────────────────────────────────────────────────────────────────
class TestHealthPublic:
    """Health check endpoint should be accessible without authentication."""

    @patch("app.GOVERNANCE_ENABLED", True)
    @patch("app.BEDROCK_MODEL", "anthropic.claude-test")
    def test_health_no_auth_header(self, client):
        """GET /health should return 200 without Authorization header."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "model" in data
        assert "governance" in data

    @patch("app.GOVERNANCE_ENABLED", True)
    @patch("app.BEDROCK_MODEL", "anthropic.claude-test")
    def test_health_with_valid_auth_header(self, client):
        """GET /health should also work with a valid Authorization header."""
        response = client.get(
            "/health",
            headers={"Authorization": "Bearer test_key_1"}
        )
        assert response.status_code == 200

    @patch("app.GOVERNANCE_ENABLED", True)
    @patch("app.BEDROCK_MODEL", "anthropic.claude-test")
    def test_health_with_invalid_auth_header(self, client):
        """GET /health should still work even with an invalid Authorization header."""
        response = client.get(
            "/health",
            headers={"Authorization": "Bearer invalid_key"}
        )
        assert response.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Test: Protected endpoints require valid Bearer token
# ─────────────────────────────────────────────────────────────────────────────
class TestProtectedEndpoints:
    """All consultation endpoints should require valid Bearer token."""

    def test_consult_schema_missing_auth(self, client, mock_env_with_keys):
        """GET /consult/schema without auth header should return 401."""
        response = client.get("/consult/schema")
        assert response.status_code == 401
        data = response.json()
        assert "Authorization" in data["detail"] or "Missing" in data["detail"]

    def test_consult_schema_invalid_token(self, client, mock_env_with_keys):
        """GET /consult/schema with invalid token should return 401."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "Bearer invalid_token_xyz"}
        )
        assert response.status_code == 401
        data = response.json()
        assert "Invalid" in data["detail"] or "token" in data["detail"].lower()

    def test_consult_schema_valid_token(self, client, mock_env_with_keys):
        """GET /consult/schema with valid token should return 200."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "Bearer test_key_1"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "patient_context_schema" in data

    def test_consult_schema_wrong_auth_scheme(self, client, mock_env_with_keys):
        """GET /consult/schema with non-Bearer scheme should return 401."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert response.status_code == 401
        data = response.json()
        assert "unsupported" in data["detail"].lower() or "scheme" in data["detail"].lower()

    def test_consult_endpoint_missing_auth(self, client, mock_env_with_keys):
        """POST /consult without auth header should return 401."""
        payload = {
            "session_id": "test-session-1",
            "transcript": "Patient reports chest pain.",
            "patient_context": {"age": 58}
        }
        response = client.post("/consult", json=payload)
        assert response.status_code == 401

    def test_consult_endpoint_valid_token(self, client, mock_env_with_keys):
        """POST /consult with valid token should be accepted (auth passes)."""
        # This test validates auth only, not the full consultation logic
        payload = {
            "session_id": "test-session-1",
            "transcript": "Patient reports chest pain.",
            "patient_context": {"age": 58}
        }
        with patch("app.run_and_stream"):  # Mock the async consultation
            response = client.post(
                "/consult",
                json=payload,
                headers={"Authorization": "Bearer test_key_2"}
            )
            # Should get 200 OK (auth passed; we're mocking the business logic)
            assert response.status_code == 200

    def test_stream_endpoint_missing_auth(self, client, mock_env_with_keys):
        """GET /stream/{session_id} without auth should return 401."""
        response = client.get("/stream/nonexistent-session")
        assert response.status_code == 401

    def test_stream_endpoint_valid_token_nonexistent_session(self, client, mock_env_with_keys):
        """GET /stream/{session_id} with valid token but nonexistent session should return 404."""
        response = client.get(
            "/stream/nonexistent-session",
            headers={"Authorization": "Bearer test_key_1"}
        )
        assert response.status_code == 404

    def test_session_endpoint_missing_auth(self, client, mock_env_with_keys):
        """GET /session/{session_id} without auth should return 401."""
        response = client.get("/session/nonexistent-session")
        assert response.status_code == 401

    def test_session_endpoint_valid_token_nonexistent_session(self, client, mock_env_with_keys):
        """GET /session/{session_id} with valid token but nonexistent session should return 404."""
        response = client.get(
            "/session/nonexistent-session",
            headers={"Authorization": "Bearer test_key_1"}
        )
        assert response.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Test: Bearer token format validation
# ─────────────────────────────────────────────────────────────────────────────
class TestBearerTokenFormat:
    """Bearer token parsing and validation edge cases."""

    def test_missing_space_in_bearer(self, client, mock_env_with_keys):
        """Authorization header with Bearer but no space should fail."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "Bearertest_key_1"}  # No space
        )
        assert response.status_code == 401

    def test_empty_bearer_token(self, client, mock_env_with_keys):
        """Authorization header with Bearer but empty token should fail."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "Bearer "}  # Empty token after space
        )
        assert response.status_code == 401

    def test_bearer_case_insensitive(self, client, mock_env_with_keys):
        """Bearer scheme should be case-insensitive."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "bearer test_key_1"}  # lowercase
        )
        assert response.status_code == 200

    def test_multiple_spaces_in_auth_header(self, client, mock_env_with_keys):
        """Authorization header with Bearer and value containing spaces should fail."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "Bearer test key with spaces"}
        )
        assert response.status_code == 401  # First part after Bearer is invalid token


# ─────────────────────────────────────────────────────────────────────────────
# Test: API key configuration edge cases
# ─────────────────────────────────────────────────────────────────────────────
class TestAPIKeyConfiguration:
    """API key configuration and fallback behavior."""

    def test_no_api_keys_configured(self, client, mock_env_no_keys):
        """When API_KEYS is empty, all protected endpoints should return 403."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "Bearer some_token"}
        )
        assert response.status_code == 403
        data = response.json()
        assert "not configured" in data["detail"].lower()

    def test_multiple_valid_keys(self, client, mock_env_with_keys):
        """All keys in API_KEYS should work."""
        for key in ["test_key_1", "test_key_2", "test_key_3"]:
            response = client.get(
                "/consult/schema",
                headers={"Authorization": f"Bearer {key}"}
            )
            assert response.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Test: WWW-Authenticate header in 401 responses
# ─────────────────────────────────────────────────────────────────────────────
class TestAuthErrorHeaders:
    """401 responses should include WWW-Authenticate header."""

    def test_missing_auth_includes_www_authenticate(self, client, mock_env_with_keys):
        """401 response for missing auth should include WWW-Authenticate header."""
        response = client.get("/consult/schema")
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_invalid_token_includes_www_authenticate(self, client, mock_env_with_keys):
        """401 response for invalid token should include WWW-Authenticate header."""
        response = client.get(
            "/consult/schema",
            headers={"Authorization": "Bearer invalid"}
        )
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers
