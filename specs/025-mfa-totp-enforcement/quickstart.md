# Quickstart: MFA TOTP Enforcement

**Branch**: `025-mfa-totp-enforcement` | **Date**: 2026-06-13

---

## Prerequisites

- `make up-auth` running (`core` + `auth` profile: Keycloak, OPA, Vault, Kong, LiteLLM, Postgres, Redis)
- Keycloak admin credentials available (`KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD` from `.env`)
- An existing test user in the `inference-platform` realm (created via Keycloak admin console or realm-export seed)
- A TOTP authenticator app installed (Google Authenticator, Authy, 1Password, etc.)

---

## Step 1 — Verify the browser-mfa Flow is Active

```bash
ADMIN_TOKEN=$(curl -s -X POST \
  "http://localhost:8083/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=${KEYCLOAK_ADMIN}&password=${KEYCLOAK_ADMIN_PASSWORD}" \
  | jq -r '.access_token')

curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8083/admin/realms/inference-platform \
  | jq '{browserFlow: .browserFlow, otpType: .otpPolicyType, otpDigits: .otpPolicyDigits, bruteForce: .bruteForceProtected}'
```

**Expected**:
```json
{
  "browserFlow": "browser-mfa",
  "otpType": "totp",
  "otpDigits": 6,
  "bruteForce": true
}
```

---

## Step 2 — Assign MFA to a Test User

```bash
# Get user ID
USER_ID=$(curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8083/admin/realms/inference-platform/users?username=testuser" \
  | jq -r '.[0].id')

# Assign CONFIGURE_TOTP required action
curl -s -X PUT \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  "http://localhost:8083/admin/realms/inference-platform/users/$USER_ID" \
  -d "{\"requiredActions\": [\"CONFIGURE_TOTP\"]}"

echo "Required action assigned to user $USER_ID"
```

---

## Step 3 — Complete TOTP Enrolment (First-Time Flow)

1. Open `http://localhost:3001` in a browser.
2. Click **Sign In** — you are redirected to the Keycloak login page.
3. Enter `testuser` credentials.
4. Keycloak detects the `CONFIGURE_TOTP` required action and presents the **Set up Authenticator Application** page.
5. Scan the QR code with your authenticator app.
6. Enter the 6-digit code shown in your app and click **Submit**.
7. You are redirected back to platform-ui with an active session.

**Verify enrolment**:
```bash
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8083/admin/realms/inference-platform/users/$USER_ID/credentials" \
  | jq '[.[] | select(.type=="otp")] | length'
# Expected: 1
```

---

## Step 4 — Verify MFA Challenge on Subsequent Logins

1. Log out from platform-ui (or clear session cookies).
2. Sign in again with `testuser` credentials.
3. After the password step, Keycloak presents the **One-time code** challenge page.
4. Enter the current 6-digit code from your authenticator app.
5. You receive an access token and are redirected to platform-ui.

**Direct grant attempt (must be blocked for browser-flow clients)**:
```bash
curl -s -X POST http://localhost:8083/realms/inference-platform/protocol/openid-connect/token \
  -d "grant_type=password&client_id=platform-ui&username=testuser&password=TestPass1!" \
  | jq '{error: .error, description: .error_description}'
# Expected for clients with direct-grant disabled:
# {"error": "unauthorized_client", "description": "..."}
```

> Note: The direct-grant flow bypasses the browser MFA step. For API clients that need TOTP-protected tokens, configure a service account + client credentials flow (no TOTP required, enforced via OPA model restrictions instead).

---

## Step 5 — Verify Incorrect TOTP Blocks Token Issuance

1. On the OTP challenge page, enter `000000` (an intentionally wrong code).
2. **Expected**: The page shows an error (`Invalid authenticator code.`) and no token is issued.
3. Repeat 4 more times.
4. **Expected after 5 failures**: Keycloak shows an account temporarily locked message.

**Check lockout state via Admin API**:
```bash
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8083/admin/realms/inference-platform/attack-detection/brute-force/users/$USER_ID" \
  | jq '{disabled: .disabled, numFailures: .numFailures}'
# Expected: {"disabled": true, "numFailures": 5}
```

**Reset lockout (admin recovery)**:
```bash
curl -s -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8083/admin/realms/inference-platform/attack-detection/brute-force/users/$USER_ID"
echo "Lockout cleared"
```

---

## Step 6 — Admin MFA Reset (Lost Authenticator Recovery)

When a user loses their authenticator device:

```bash
# List the user's OTP credentials
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8083/admin/realms/inference-platform/users/$USER_ID/credentials" \
  | jq '.[] | select(.type=="otp") | {id, userLabel, createdDate}'

# Remove a specific OTP credential
CRED_ID=<credential-id-from-above>
curl -s -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8083/admin/realms/inference-platform/users/$USER_ID/credentials/$CRED_ID"

# Re-assign CONFIGURE_TOTP required action so user must re-enrol
curl -s -X PUT \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  "http://localhost:8083/admin/realms/inference-platform/users/$USER_ID" \
  -d '{"requiredActions": ["CONFIGURE_TOTP"]}'

echo "User MFA reset complete — user must re-enrol on next login"
```

---

## Step 7 — Smoke Test (Automated)

Run the full MFA smoke test suite:

```bash
make smoke
```

The smoke test script (`scripts/smoke-test.sh`) includes:
- Verifying `browser-mfa` flow is bound (`GET /admin/realms/inference-platform`)
- Verifying OTP policy fields match the contract
- Verifying brute-force protection is enabled with `failureFactor=5`
- Verifying a Kong request without a valid JWT returns `401`

---

## Memory Budget Check

```bash
make stats
```

MFA enforcement adds no new containers. The `up-auth` profile memory footprint is unchanged from feature 024 (~2.81 GB total). If stats show >2.9 GB, stop non-essential profiles before running MFA tests.
