"""
app_auth.py — Bearer token authentication for Aura Health API.

Provides FastAPI dependency injection for route-level authentication.
Validates incoming Authorization: Bearer <token> headers against allowed API keys.

All API keys are loaded from the API_KEYS environment variable (comma-separated).
Health check endpoint (/health) is always exempted from authentication.

Usage in app.py routes:
    @api.get("/consult/schema")
    async def consult_schema(auth: str = Depends(verify_bearer_token)):
        ...

    @api.get("/health")
    def health():  # No Depends() — unauthenticated
        ...
"""

import os
from fastapi import HTTPException, Header, status


def get_api_keys() -> list:
    """Load comma-separated API keys from environment variable."""
    raw = os.getenv("API_KEYS", "").strip()
    if not raw:
        raise ValueError(
            "API_KEYS environment variable not set. "
            "Set to comma-separated list of valid bearer tokens, e.g.: "
            "API_KEYS=key1,key2,key3"
        )
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise ValueError("API_KEYS environment variable is empty.")
    return keys


# Load keys once at module initialization for efficiency.
try:
    VALID_API_KEYS = get_api_keys()
    print(f"API authentication enabled: {len(VALID_API_KEYS)} valid keys configured.")
except ValueError as e:
    # If running without API_KEYS set, allow graceful degradation for testing.
    # In production, this should fail hard at startup — but the dependency
    # injection will catch missing tokens and return 401 anyway.
    VALID_API_KEYS = []
    print(f"WARNING: {e}")
    print("Running in degraded mode — all authenticated endpoints will reject all requests.")


def verify_bearer_token(authorization: str = Header(None)) -> str:
    """
    FastAPI dependency to validate Bearer token from Authorization header.

    Returns the valid token (for logging/auditing purposes).
    Raises HTTPException with:
        - 401 Unauthorized if token is missing or invalid
        - 403 Forbidden if API authentication is not configured

    Usage:
        @api.get("/protected")
        async def protected_route(token: str = Depends(verify_bearer_token)):
            # token is the validated bearer token
            return {"message": "access granted"}

    Examples:
        ✓ Authorization: Bearer valid_token_here
        ✗ Authorization: Bearer invalid_token
        ✗ (missing Authorization header)
        ✗ Authorization: Basic user:password
    """

    # Check if API authentication is configured
    if not VALID_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API authentication is not configured on the server.",
        )

    # Check if Authorization header exists
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Use: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Parse Bearer token
    try:
        scheme, token = authorization.split(" ", 1)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Use: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unsupported authentication scheme '{scheme}'. Use 'Bearer'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Validate token against allowed list
    if token not in VALID_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token


def verify_bearer_token_or_query(authorization: str = Header(None), token: str = None) -> str:
    """
    FastAPI dependency for SSE/EventSource endpoints.
    
    Accepts authentication from EITHER:
    1. Authorization header: Bearer <token> (standard)
    2. Query parameter: ?token=<token> (for EventSource which doesn't support custom headers)
    
    Returns the valid token.
    Raises HTTPException with 401/403 if token is invalid or missing.
    
    Usage:
        @api.get("/stream/{session_id}")
        async def stream(session_id: str, auth: str = Depends(verify_bearer_token_or_query)):
            # auth is the validated bearer token
    """
    
    # Check if API authentication is configured
    if not VALID_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API authentication is not configured on the server.",
        )
    
    # Try header first (standard approach)
    if authorization:
        try:
            scheme, bearer_token = authorization.split(" ", 1)
            if scheme.lower() == "bearer" and bearer_token in VALID_API_KEYS:
                return bearer_token
        except ValueError:
            pass  # Fall through to query param check
    
    # Fall back to query parameter (for EventSource)
    if token:
        if token in VALID_API_KEYS:
            return token
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired bearer token.",
            )
    
    # No auth found
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing Authorization header or 'token' query parameter. Use: Authorization: Bearer <token> or ?token=<token>",
        headers={"WWW-Authenticate": "Bearer"},
    )
