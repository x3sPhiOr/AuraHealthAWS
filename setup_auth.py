#!/usr/bin/env python3
"""
Setup and test authentication with sample API keys.
"""

import os
import subprocess
import sys

def set_api_keys():
    """Set test API keys in environment."""
    test_keys = "sk_test_1,sk_test_2,sk_test_3"
    os.environ["API_KEYS"] = test_keys
    print(f"✓ API_KEYS set to: {test_keys}")
    return test_keys

def test_auth():
    """Test authentication module with set keys."""
    print("\n" + "=" * 70)
    print("TESTING AUTHENTICATION WITH CONFIGURED KEYS")
    print("=" * 70)
    
    # Reimport to get fresh module state with new environment
    import importlib
    import app_auth
    importlib.reload(app_auth)
    
    from app_auth import VALID_API_KEYS, verify_bearer_token
    
    print(f"\nConfigured keys: {VALID_API_KEYS}")
    
    test_cases = [
        ("sk_test_1", "Valid token (sk_test_1)", True),
        ("sk_test_2", "Valid token (sk_test_2)", True),
        ("invalid_xyz", "Invalid token", False),
        (None, "Missing token", False),
    ]
    
    print("\n" + "-" * 70)
    print("Test Results:")
    print("-" * 70)
    
    results = []
    for token, description, should_pass in test_cases:
        if token is None:
            auth_header = None
        else:
            auth_header = f"Bearer {token}"
        
        try:
            result = verify_bearer_token(authorization=auth_header)
            status = "✓ PASS (200)" if should_pass else "✗ FAIL (should reject)"
            results.append((status, description))
            print(f"{status:20} | {description}")
        except Exception as e:
            status_code = getattr(e, 'status_code', '???')
            status = "✓ PASS (reject)" if not should_pass else f"✗ FAIL ({status_code})"
            results.append((status, description))
            print(f"{status:20} | {description}")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum(1 for r in results if "PASS" in r[0])
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("\n✓ Authentication is working correctly!")
        print("✓ You can now start the app and test via curl or test_client.html")
    else:
        print("\n✗ Some tests failed. Check app_auth.py configuration.")

if __name__ == "__main__":
    set_api_keys()
    test_auth()
    
    print("\n" + "=" * 70)
    print("NEXT STEPS")
    print("=" * 70)
    print("1. Set API_KEYS environment variable before running app.py:")
    print("   • Windows: $env:API_KEYS='sk_test_1,sk_test_2,sk_test_3'")
    print("   • Linux:   export API_KEYS='sk_test_1,sk_test_2,sk_test_3'")
    print("")
    print("2. Start the app: python app.py")
    print("")
    print("3. In another terminal, test endpoints:")
    print("   • Valid:   curl -H 'Authorization: Bearer sk_test_1' http://localhost:8000/consult/schema")
    print("   • Invalid: curl -H 'Authorization: Bearer bad_key' http://localhost:8000/consult/schema")
    print("             → Should get 401 Unauthorized")
    print("")
    print("4. Or use the browser: open test_client.html and enter a key")
    print("=" * 70)
