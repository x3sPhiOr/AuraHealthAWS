# Bearer Token Authentication Implementation — Complete

## Summary

Your Aura Health API now requires Bearer token authentication for all endpoints except `/health`.

### What Changed

**New Files:**
- `app_auth.py` — Bearer token validation module with FastAPI dependency injection
- `tests/test_auth.py` — Comprehensive authentication test suite (30+ tests)

**Modified Files:**
- `app.py` — Added authentication to 5 protected endpoints:
  - `POST /consult`
  - `GET /consult/schema`
  - `GET /stream/{session_id}`
  - `GET /session/{session_id}`
  - `POST /consult/audio`
  
- `API.md` — New "Authentication" section with examples and error codes

**Unchanged:** `/health` endpoint remains public (no auth required for App Runner health checks)

---

## Quick Start

### Local Testing

1. **Set API keys environment variable:**
   ```bash
   export API_KEYS="sk_test_1,sk_test_2,sk_test_3"
   ```

2. **Start the server:**
   ```bash
   python app.py
   ```

3. **Run authentication tests:**
   ```bash
   pytest tests/test_auth.py -v
   ```

4. **Test endpoints manually:**
   ```bash
   # ✓ Health check — no auth required
   curl http://localhost:8000/health
   
   # ✗ Missing token
   curl http://localhost:8000/consult/schema
   # → 401 Unauthorized
   
   # ✓ Valid token
   curl -H "Authorization: Bearer sk_test_1" \
     http://localhost:8000/consult/schema
   # → 200 OK
   
   # ✗ Invalid token
   curl -H "Authorization: Bearer invalid_key" \
     http://localhost:8000/consult/schema
   # → 401 Unauthorized
   ```

---

## Deployment to AWS App Runner

### 1. Update Service Configuration

Edit `deploy/apprunner-service.json.template` to inject API keys from Secrets Manager:

```json
{
  "ServiceName": "aura-health",
  "SourceConfiguration": {
    "ImageRepository": {
      "ImageIdentifier": "<account-id>.dkr.ecr.<region>.amazonaws.com/aura-health:latest"
    }
  },
  "InstanceConfiguration": {
    "InstanceRoleArn": "<IAM-role-arn>"
  },
  "AutoScalingConfigurationArn": "<ASC-arn>",
  "EnvironmentVariables": [
    {
      "Name": "AWS_DEFAULT_REGION",
      "Value": "ap-southeast-1"
    },
    {
      "Name": "BEDROCK_MODEL",
      "Value": "anthropic.claude-haiku-4-5-20251001-v1:0"
    }
  ],
  "SecretsManagerVariables": [
    {
      "Name": "API_KEYS",
      "ValueFrom": "arn:aws:secretsmanager:<region>:<account>:secret:aura-health/api-keys:API_KEYS::"
    }
  ]
}
```

### 2. Create Secrets Manager Entry

Store API keys in AWS Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name aura-health/api-keys \
  --region ap-southeast-1 \
  --secret-string '{
    "API_KEYS": "sk_live_abc123xyz,sk_live_def456uvw,sk_live_ghi789rst"
  }'
```

Or update existing secret:

```bash
aws secretsmanager update-secret \
  --secret-id aura-health/api-keys \
  --secret-string '{
    "API_KEYS": "sk_live_abc123xyz,sk_live_def456uvw,sk_live_ghi789rst"
  }'
```

### 3. Update App Runner Service

Deploy with updated configuration:

```bash
aws apprunner update-service \
  --service-arn arn:aws:apprunner:<region>:<account>:service/aura-health \
  --source-configuration file://deploy/apprunner-service.json \
  --region ap-southeast-1
```

### 4. Verify Health Check Still Works

App Runner will automatically poll `/health` without credentials:

```bash
curl https://<service-id>.ap-southeast-1.awsapprunner.com/health
# → {"status": "ok", "model": "...", "governance": true}
```

---

## API Key Rotation

**To rotate API keys without downtime:**

1. Add new key to Secrets Manager (keep old key temporarily):
   ```bash
   API_KEYS=sk_live_abc123xyz,sk_live_def456uvw,sk_live_NEW_KEY
   ```

2. Update App Runner service
3. Update all clients to use new keys (grace period = 5 minutes recommended)
4. Remove old key from Secrets Manager:
   ```bash
   API_KEYS=sk_live_NEW_KEY
   ```

---

## Client Integration

**JavaScript/Node.js example:**

```javascript
const response = await fetch('https://api.aura-health.com/consult/schema', {
  headers: {
    'Authorization': 'Bearer sk_live_abc123xyz'
  }
});

if (response.status === 401) {
  console.error('Invalid or missing API key');
}
```

**Python example:**

```python
import requests

headers = {'Authorization': 'Bearer sk_live_abc123xyz'}
response = requests.get(
  'https://api.aura-health.com/consult/schema',
  headers=headers
)

if response.status_code == 401:
    print('Invalid or missing API key')
```

**cURL example:**

```bash
curl -H "Authorization: Bearer sk_live_abc123xyz" \
  https://api.aura-health.com/consult
```

---

## Error Responses

**401 Unauthorized — Missing token:**
```json
{
  "detail": "Missing Authorization header. Use: Authorization: Bearer <token>"
}
```
Headers: `WWW-Authenticate: Bearer`

**401 Unauthorized — Invalid token:**
```json
{
  "detail": "Invalid or expired bearer token."
}
```
Headers: `WWW-Authenticate: Bearer`

**403 Forbidden — Not configured:**
```json
{
  "detail": "API authentication is not configured on the server."
}
```

---

## Security Notes

✓ Tokens are validated on every request (no caching vulnerabilities)
✓ All tokens share the same permission level (authentication-only, not role-based)
✓ Tokens in URL parameters are NOT supported — only Authorization header (prevents accidental logging in access logs)
✓ /health remains public for health-check integrations (load balancers, App Runner status)
✓ No token expiration implemented yet (all keys are equally valid indefinitely) — recommend adding `expires_at` field for future phases

---

## Next Steps (Optional Enhancements)

1. **Token Expiration** — Add `expires_at` field to API key store (DynamoDB or Secrets Manager)
2. **Rate Limiting** — Add per-token rate limiting to prevent abuse
3. **Audit Trail Integration** — Update `governance/audit_log.py` to include bearer token (masked) in session audit records
4. **Role-Based Access Control** — Store per-token permissions and implement authorization layer
5. **API Key Logging** — Add metrics for key usage (which endpoints, success/failure rates)
6. **Key Versioning** — Support multiple valid keys per service (useful for canary deployments)

---

## Files Reference

- [app_auth.py](../app_auth.py) — Authentication module (80 lines)
- [app.py](../app.py) — 5 endpoints updated with `Depends(verify_bearer_token)` (lines in methods)
- [API.md](../API.md) — New "Authentication" section (40 lines)
- [tests/test_auth.py](../tests/test_auth.py) — Test suite (250 lines, 30+ tests)
- [deploy/apprunner-service.json.template](../deploy/apprunner-service.json.template) — Example config (recommended updates)

---

## Support

**Debugging:**

To see which tokens are loaded at startup, check the initialization output:
```
API authentication enabled: 3 valid keys configured.
```

**Fallback for testing:**

If `API_KEYS` is not set, all authenticated endpoints will return 403:
```json
{
  "detail": "API authentication is not configured on the server."
}
```

This ensures you never accidentally expose an unauthenticated API to production.
