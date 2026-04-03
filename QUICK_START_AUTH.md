# Quick Start: API Authentication Setup

## Option 1: Test with Environment Variable (Quick)

### Windows PowerShell
```powershell
# Set environment variable for current session
$env:API_KEYS = "test_key_1,test_key_2,test_key_3"

# Verify it's set
Write-Host $env:API_KEYS

# Start the app
python app.py
```

### Windows Command Prompt (cmd.exe)
```cmd
# Set environment variable for current session
set API_KEYS=test_key_1,test_key_2,test_key_3

# Verify it's set
echo %API_KEYS%

# Start the app
python app.py
```

### Linux/macOS Bash
```bash
# Set environment variable for current session
export API_KEYS="test_key_1,test_key_2,test_key_3"

# Verify it's set
echo $API_KEYS

# Start the app
python app.py
```

---

## Option 2: Persistent Configuration (.env file)

Add to your `.env` file in the project root:
```ini
API_KEYS=test_key_1,test_key_2,test_key_3
```

Then start the app normally:
```bash
python app.py
```

---

## Testing Authentication

Once the app is running with `API_KEYS` set:

### 1. Health endpoint (no auth required)
```bash
curl http://localhost:8000/health
# Should return: 200 OK
```

### 2. Protected endpoint with valid token
```bash
# Replace 'test_key_1' with one of your configured keys
curl -H "Authorization: Bearer test_key_1" http://localhost:8000/consult/schema
# Should return: 200 OK with schema
```

### 3. Protected endpoint with invalid token
```bash
curl -H "Authorization: Bearer invalid_token_xyz" http://localhost:8000/consult/schema
# Should return: 401 Unauthorized ← This is what you want!
```

### 4. Protected endpoint with missing token
```bash
curl http://localhost:8000/consult/schema
# Should return: 401 Unauthorized
```

---

## What Each Response Means

| Scenario | Response | Reason |
|----------|----------|--------|
| Valid token | **200 OK** | Token matches configured key |
| Invalid token | **401 Unauthorized** | Token doesn't match any configured key |
| Missing token | **401 Unauthorized** | No Authorization header provided |
| No API_KEYS set | **403 Forbidden** | Auth not configured (degraded mode) |
| /health endpoint | **200 OK** | Health check is always public |

---

## Current Test Client Status

The test client (`test_client.html`) is ready to use once you:
1. Set `API_KEYS` environment variable
2. Restart `app.py`
3. Open `test_client.html` in a browser
4. Enter a valid API key in the "API KEY" field
5. Make requests through the UI
