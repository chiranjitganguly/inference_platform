# Contract: Keycloak browser-mfa Authentication Flow

**Branch**: `025-mfa-totp-enforcement` | **Date**: 2026-06-13

This document defines the declarative contract for the `browser-mfa` Keycloak authentication flow and the OTP policy that backs it. The implementation target is `services/keycloak/realm-export.json`.

---

## Flow Definition Contract

The `browser-mfa` flow MUST be present in the realm export with the following structure. Alias names and requirement levels are binding; display order within each sub-flow is informational.

```json
{
  "alias": "browser-mfa",
  "description": "Browser login with mandatory TOTP second factor (conditional per-user)",
  "providerId": "basic-flow",
  "topLevel": true,
  "builtIn": false,
  "authenticationExecutions": [
    {
      "authenticatorConfig": null,
      "authenticator": "auth-cookie",
      "requirement": "ALTERNATIVE",
      "priority": 10
    },
    {
      "authenticatorConfig": null,
      "authenticator": "auth-spnego",
      "requirement": "DISABLED",
      "priority": 20
    },
    {
      "authenticatorConfig": null,
      "authenticator": "identity-provider-redirector",
      "requirement": "ALTERNATIVE",
      "priority": 25
    },
    {
      "flowAlias": "browser-mfa forms",
      "requirement": "ALTERNATIVE",
      "priority": 30,
      "authenticationExecutions": [
        {
          "authenticator": "auth-username-password-form",
          "requirement": "REQUIRED",
          "priority": 10
        },
        {
          "flowAlias": "browser-mfa - Conditional OTP",
          "requirement": "CONDITIONAL",
          "priority": 20,
          "authenticationExecutions": [
            {
              "authenticator": "condition-user-configured",
              "requirement": "REQUIRED",
              "priority": 10
            },
            {
              "authenticator": "auth-otp-form",
              "requirement": "REQUIRED",
              "priority": 20
            }
          ]
        }
      ]
    }
  ]
}
```

### Flow Binding

```json
{
  "browserFlow": "browser-mfa"
}
```

This binding MUST replace the default `"browser"` flow binding on the realm.

---

## OTP Policy Contract

```json
{
  "otpPolicyType": "totp",
  "otpPolicyAlgorithm": "HmacSHA1",
  "otpPolicyInitialCounter": 0,
  "otpPolicyDigits": 6,
  "otpPolicyLookAheadWindow": 1,
  "otpPolicyPeriod": 30,
  "otpPolicyCodeReusable": false
}
```

**Invariants**:
- `otpPolicyType` MUST be `"totp"` (not `"hotp"`).
- `otpPolicyDigits` MUST be `6`.
- `otpPolicyPeriod` MUST be `30` (seconds).
- `otpPolicyCodeReusable` MUST be `false` (replay prevention, FR-007).
- `otpPolicyLookAheadWindow` MUST be `>= 1` (clock drift tolerance, FR-008).

---

## Brute-Force Protection Contract

```json
{
  "bruteForceProtected": true,
  "permanentLockout": false,
  "failureFactor": 5,
  "maxFailureWaitSeconds": 900,
  "waitIncrementSeconds": 60,
  "minimumQuickLoginWaitSeconds": 60,
  "quickLoginCheckMilliSeconds": 1000,
  "maxDeltaTimeSeconds": 43200
}
```

**Invariants**:
- `bruteForceProtected` MUST be `true`.
- `failureFactor` MUST be `5` (FR-009 requires lockout after 5 consecutive failures).
- `permanentLockout` MUST be `false` (time-limited lockout, not permanent ban).

---

## Required Actions Contract

To enforce MFA for a specific user, the admin assigns the `CONFIGURE_TOTP` required action:

**Via Keycloak Admin REST API**:
```
PUT /admin/realms/inference-platform/users/{userId}
{
  "requiredActions": ["CONFIGURE_TOTP"]
}
```

**Via realm-export default** (opt-in all new users):
```json
{
  "defaultRequiredActions": []
}
```
Leave empty for v1 (admin-assigned per user). Set to `["CONFIGURE_TOTP"]` to auto-enrol all new accounts.

---

## Acceptance Tests

```bash
# 1. Flow exists and is bound as browser flow
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8083/admin/realms/inference-platform \
  | jq '.browserFlow'
# Expected: "browser-mfa"

# 2. OTP policy is correct
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8083/admin/realms/inference-platform \
  | jq '{type: .otpPolicyType, period: .otpPolicyPeriod, digits: .otpPolicyDigits, reusable: .otpPolicyCodeReusable}'
# Expected: {"type":"totp","period":30,"digits":6,"reusable":false}

# 3. Brute-force protection enabled with threshold 5
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://localhost:8083/admin/realms/inference-platform \
  | jq '{protected: .bruteForceProtected, threshold: .failureFactor}'
# Expected: {"protected":true,"threshold":5}

# 4. Password-only login returns challenge (no token) for MFA-enrolled user
# (requires an enrolled test user; see quickstart.md for setup)
TOKEN=$(curl -s -X POST http://localhost:8083/realms/inference-platform/protocol/openid-connect/token \
  -d "grant_type=password&client_id=platform-ui&username=mfa-test-user&password=TestPass1!" \
  | jq -r '.access_token')
echo "$TOKEN"
# Expected: null (MFA challenge required — direct grant blocked by MFA policy)
```
