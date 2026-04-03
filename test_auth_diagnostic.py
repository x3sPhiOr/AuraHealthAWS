#!/usr/bin/env python3
"""
Diagnostic script to test API authentication without running the full app.
Tests the app_auth module directly and checks environment setup.
"""

import os
import sys

# Diagnostic 1: Check if API_KEYS environment variable is set
print("=" * 70)
print("DIAGNOSTIC 1: Environment Variable Check")
print("=" * 70)
api_keys_env = os.getenv("API_KEYS", "")
if api_keys_env:
    print(f"✓ API_KEYS is SET")
    keys_list = [k.strip() for k in api_keys_env.split(",") if k.strip()]
    print(f"  → {len(keys_list)} valid keys configured")
    print(f"  → Keys: {', '.join(f'***' for _ in keys_list)}")  # Hide actual keys for security
else:
    print("✗ API_KEYS is NOT SET")
    print("  → Authentication will be in degraded mode (all requests rejected with 403)")

# Diagnostic 2: Import app_auth and check VALID_API_KEYS
print("\n" + "=" * 70)
print("DIAGNOSTIC 2: app_auth Module Check")
print("=" * 70)
try:
    from app_auth import VALID_API_KEYS, verify_bearer_token
    print(f"✓ app_auth imported successfully")
    print(f"  → VALID_API_KEYS has {len(VALID_API_KEYS)} configured keys")
except ImportError as e:
    print(f"✗ app_auth import FAILED: {e}")
    sys.exit(1)

# Diagnostic 3: Test verify_bearer_token function directly
print("\n" + "=" * 70)
print("DIAGNOSTIC 3: verify_bearer_token Function Test")
print("=" * 70)

test_cases = [
    (None, "Missing Authorization header"),
    ("", "Empty Authorization header"),
    ("invalid_token", "No Bearer prefix"),
    ("Bearer invalid_key_12345", "Invalid bearer token"),
    ("Bearer", "Malformed bearer token (no value)"),
    ("BasicAuth user:pass", "Wrong auth scheme"),
]

if VALID_API_KEYS:
    test_cases.append((f"Bearer {VALID_API_KEYS[0]}", "Valid bearer token"))

for auth_header, description in test_cases:
    try:
        result = verify_bearer_token(authorization=auth_header)
        print(f"✓ {description}: SUCCESS (returned token)")
    except Exception as e:
        status_code = getattr(e, 'status_code', '???')
        detail = getattr(e, 'detail', str(e))
        print(f"✗ {description}: {status_code} - {detail}")

# Diagnostic 5: Summary & Recommendations
print("\n" + "=" * 70)
print("SUMMARY & RECOMMENDATIONS")
print("=" * 70)
if not api_keys_env:
    print("⚠️  API_KEYS is not set in environment")
    print("   → Set it before running: export API_KEYS='key1,key2,key3'")
    print("   → Or in your .env file: API_KEYS=key1,key2,key3")
elif not VALID_API_KEYS:
    print("⚠️  VALID_API_KEYS is empty despite API_KEYS being set")
    print("   → Check API_KEYS format: must be 'key1,key2,key3' (comma-separated)")
    print("   → Each key must not be empty after stripping whitespace")
else:
    print("✓ Authentication is properly configured")
    print(f"  → {len(VALID_API_KEYS)} valid keys ready to use")
    if len(VALID_API_KEYS) == 1:
        print("  ⚠️ Only 1 key configured — consider adding more for production")

print("\n" + "=" * 70)
print("To test HTTP endpoints:")
print("  1. Start the app: python app.py")
print("  2. In another terminal: python tests/test_auth.py")
print("=" * 70)
