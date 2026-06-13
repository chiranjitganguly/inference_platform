# Tasks: MFA TOTP Enforcement

**Input**: Design documents from `specs/025-mfa-totp-enforcement/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅ | quickstart.md ✅

**Implementation type**: Configuration-only — all changes are confined to `services/keycloak/realm-export.json` and `scripts/smoke-test.sh`. No new services, no new Docker images, no new database tables.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: User story label (US1, US2, US3)

---

## Phase 1: Setup (Read & Understand Current State)

**Purpose**: Understand the existing `realm-export.json` structure before making targeted edits. The plan flags a key risk: the realm-export is a full snapshot from feature 024 — edits must be surgical to avoid clobbering existing client definitions, Kong JWT consumers, and route mappings.

- [x] T001 Read services/keycloak/realm-export.json to locate the existing `browser` authentication flow definition and note the JSON structure, execution array format, and all existing flow aliases
- [x] T002 [P] Read services/keycloak/realm-export.json to locate existing OTP policy fields (`otpPolicyType`, `otpPolicyAlgorithm`, `otpPolicyDigits`, `otpPolicyPeriod`, `otpPolicyLookAheadWindow`, `otpPolicyCodeReusable`) and note current values
- [x] T003 [P] Read services/keycloak/realm-export.json to confirm `browserFlow` binding field and current value, and confirm `bruteForceProtected` and `failureFactor` fields are present or absent

**Checkpoint**: Current realm-export.json structure understood — ready for targeted edits

---

## Phase 2: Foundational (Keycloak Realm Export — Blocking Prerequisites)

**Purpose**: Core `realm-export.json` changes that ALL three user stories depend on. No story acceptance test can pass until this phase is complete.

**⚠️ CRITICAL**: All user story phases (3–5) are blocked until this phase completes.

- [x] T004 In services/keycloak/realm-export.json, add the `browser-mfa` authentication flow to the `authenticationFlows` array with the exact structure from `specs/025-mfa-totp-enforcement/contracts/keycloak-flow.md` — include the top-level `browser-mfa` flow, the `browser-mfa forms` sub-flow (auth-username-password-form REQUIRED), and the `browser-mfa - Conditional OTP` sub-flow (condition-user-configured REQUIRED + auth-otp-form REQUIRED, outer requirement CONDITIONAL)
- [x] T005 In services/keycloak/realm-export.json, update the `browserFlow` field to `"browser-mfa"` (replaces the existing `"browser"` binding)
- [x] T006 [P] In services/keycloak/realm-export.json, set all OTP policy fields to contract values: `"otpPolicyType": "totp"`, `"otpPolicyAlgorithm": "HmacSHA1"`, `"otpPolicyDigits": 6`, `"otpPolicyPeriod": 30`, `"otpPolicyLookAheadWindow": 1`, `"otpPolicyCodeReusable": false`
- [x] T007 [P] In services/keycloak/realm-export.json, set brute-force protection fields: `"bruteForceProtected": true`, `"permanentLockout": false`, `"failureFactor": 5`, `"maxFailureWaitSeconds": 900`, `"waitIncrementSeconds": 60`, `"minimumQuickLoginWaitSeconds": 60`, `"quickLoginCheckMilliSeconds": 1000`, `"maxDeltaTimeSeconds": 43200`
- [x] T008 Validate services/keycloak/realm-export.json is valid JSON by running: `jq empty services/keycloak/realm-export.json && echo "JSON valid"` — fix any parse errors before proceeding
- [ ] T009 Restart Keycloak to import the updated realm: `make restart svc=keycloak` then wait for healthy status with `docker inspect --format='{{.State.Health.Status}}' inference_platform-keycloak-1` — expected: `healthy`  ⏳ REQUIRES: make up-auth
- [ ] T010 Verify the `browser-mfa` flow is bound as the realm browser flow: `curl -s -H "Authorization: Bearer $(curl -s -X POST http://localhost:8083/realms/master/protocol/openid-connect/token -d "grant_type=password&client_id=admin-cli&username=${KEYCLOAK_ADMIN}&password=${KEYCLOAK_ADMIN_PASSWORD}" | jq -r '.access_token')" http://localhost:8083/admin/realms/inference-platform | jq '.browserFlow'` — expected: `"browser-mfa"`  ⏳ REQUIRES: make up-auth

**Checkpoint**: browser-mfa flow is live, OTP policy and brute-force protection are active — user story verification can begin

---

## Phase 3: User Story 1 — First-Time MFA Enrolment (Priority: P1) 🎯 MVP

**Goal**: A user with no TOTP enrolled is prompted to set up their authenticator inline during login before receiving an access token.

**Independent Test**: Create a fresh test user, assign CONFIGURE_TOTP required action, log in via platform-ui, scan QR code, enter TOTP code — verify OTP credential count == 1 and access token is issued.

- [ ] T011 [US1] Obtain an admin token and create a test user `mfa-test-enrol` in the `inference-platform` realm via Keycloak Admin REST API: `POST /admin/realms/inference-platform/users` with `{"username":"mfa-test-enrol","enabled":true,"credentials":[{"type":"password","value":"TestMFA1!","temporary":false}]}`
- [ ] T012 [US1] Assign the `CONFIGURE_TOTP` required action to `mfa-test-enrol` via Keycloak Admin REST API: `PUT /admin/realms/inference-platform/users/{userId}` with `{"requiredActions":["CONFIGURE_TOTP"]}` — verify the response with `GET /admin/realms/inference-platform/users/{userId}` shows `"requiredActions":["CONFIGURE_TOTP"]`
- [ ] T013 [US1] Open `http://localhost:3001` in a browser, click Sign In, enter credentials for `mfa-test-enrol` — verify the Keycloak page shows **Set up Authenticator Application** (QR code screen) and does NOT issue an access token or redirect to platform-ui home
- [ ] T014 [US1] Scan the QR code with a TOTP authenticator app, enter the displayed 6-digit code in the Keycloak enrolment form, submit — verify Keycloak redirects back to platform-ui with an active session (user is logged in)
- [ ] T015 [US1] Verify enrolment completion: `curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "http://localhost:8083/admin/realms/inference-platform/users/$USER_ID/credentials" | jq '[.[] | select(.type=="otp")] | length'` — expected: `1`
- [ ] T016 [US1] Verify the required action is cleared post-enrolment: `curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "http://localhost:8083/admin/realms/inference-platform/users/$USER_ID" | jq '.requiredActions'` — expected: `[]`

**Checkpoint**: User Story 1 complete — first-time enrolment flow works end-to-end

---

## Phase 4: User Story 2 — Returning User TOTP Challenge (Priority: P1)

**Goal**: An already-enrolled user must complete the TOTP challenge on every subsequent login before receiving an access token.

**Independent Test**: Log out from platform-ui and log back in as the enrolled test user — verify OTP challenge is shown after password, token issued only after valid code, rejected on invalid code.

- [ ] T017 [US2] Log out of platform-ui (or clear browser session cookies for `localhost:3001`), then sign in again as `mfa-test-enrol` with password — verify the Keycloak page shows the **One-time code** challenge form (not the home page)
- [ ] T018 [US2] Enter the current 6-digit TOTP code from the authenticator app and submit — verify platform-ui home page loads with an active session (access token issued)
- [ ] T019 [US2] Log out again, sign in, and at the OTP challenge enter `000000` (intentionally wrong) — verify the Keycloak page shows `Invalid authenticator code.` error and no access token is issued and no redirect to platform-ui home occurs (FR-010)
- [ ] T020 [US2] Verify empty code submission is rejected: at the OTP challenge form, submit with blank code — verify an error is shown and no token issued

**Checkpoint**: User Story 2 complete — enrolled users always face the TOTP challenge; wrong codes never produce tokens

---

## Phase 5: User Story 3 — MFA Bypass Attempt Blocked (Priority: P1)

**Goal**: Clients cannot obtain an access token through any path without completing the TOTP step for MFA-enrolled users. Five consecutive failures lock the session.

**Independent Test**: Direct grant without TOTP returns no token; five failed OTP attempts produce a lockout state verifiable via the Admin API attack-detection endpoint.

- [ ] T021 [US3] Attempt a direct-grant token request for `mfa-test-enrol` without completing the browser flow: `curl -s -X POST http://localhost:8083/realms/inference-platform/protocol/openid-connect/token -d "grant_type=password&client_id=platform-ui&username=mfa-test-enrol&password=TestMFA1!" | jq '{token: .access_token, error: .error}'` — expected: `{"token": null, "error": "..."}` (direct-grant disabled for browser-flow clients, or MFA challenge cannot be satisfied via direct-grant)
- [ ] T022 [US3] Verify Kong correctly rejects requests without a valid JWT: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/v1/models` — expected: `401`
- [ ] T023 [US3] Trigger brute-force lockout by submitting wrong TOTP code 5 times consecutively at the Keycloak OTP challenge form for `mfa-test-enrol`
- [ ] T024 [US3] Verify lockout state via Admin API: `curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "http://localhost:8083/admin/realms/inference-platform/attack-detection/brute-force/users/$USER_ID" | jq '{disabled: .disabled, numFailures: .numFailures}'` — expected: `{"disabled": true, "numFailures": 5}`
- [ ] T025 [US3] Verify no access token is issued while account is locked: attempt login via platform-ui — verify Keycloak shows account temporarily disabled message
- [ ] T026 [US3] Reset lockout for cleanup: `curl -s -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" "http://localhost:8083/admin/realms/inference-platform/attack-detection/brute-force/users/$USER_ID"` — verify normal login works again after reset

**Checkpoint**: User Story 3 complete — all bypass paths blocked, brute-force lockout confirmed

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Automate acceptance checks, update documentation, confirm memory budget.

- [x] T027 Add MFA acceptance checks to scripts/smoke-test.sh: (1) verify `browserFlow == "browser-mfa"` via Keycloak Admin API, (2) verify `otpPolicyType == "totp"` and `otpPolicyCodeReusable == false`, (3) verify `bruteForceProtected == true` and `failureFactor == 5` — each check must print PASS/FAIL and exit 1 on any failure
- [ ] T028 Run `make smoke` and verify all checks pass including the new MFA checks — fix any failures before proceeding  ⏳ REQUIRES: make up-auth
- [ ] T029 [P] Run `make stats` and confirm the `up-auth` profile total memory stays within the ~2.81 GB budget — document the observed value in `docs/progress.md`  ⏳ REQUIRES: make up-auth
- [x] T030 Update `docs/progress.md` to mark feature 025 as complete: record the actual `make smoke` pass timestamp, observed memory stat, and Keycloak `browserFlow` API response as falsifiable evidence

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately; T001, T002, T003 can all run in parallel
- **Foundational (Phase 2)**: Depends on Phase 1 understanding — T004–T007 can run in parallel (different JSON sections); T008 depends on T004–T007; T009 depends on T008; T010 depends on T009
- **User Stories (Phase 3–5)**: All depend on T010 (Keycloak healthy with new flow) — stories 1, 2, 3 proceed sequentially because US2 requires an enrolled user from US1, and US3 tests the same enrolled user
- **Polish (Phase 6)**: Depends on all user story checkpoints passing

### User Story Dependencies

- **US1 (First-Time Enrolment)**: Depends on Phase 2 complete — creates the enrolled test user relied on by US2 and US3
- **US2 (Returning User Challenge)**: Depends on US1 complete (requires enrolled test user from T011–T014)
- **US3 (Bypass Blocking)**: Depends on US1 complete (requires enrolled test user); can run concurrently with US2 tasks on separate browser sessions

### Within Each Phase

- T001, T002, T003 (Setup): all parallel — reading only
- T004, T005, T006, T007 (Foundational): T005/T006/T007 parallel with each other (different JSON fields); T004 should precede T005 if editing the same flow section
- T008 must follow T004–T007
- T009 must follow T008
- T010 must follow T009

### Parallel Opportunities

```bash
# Phase 1 — all three reads in parallel
T001 + T002 + T003

# Phase 2 — JSON edits in parallel (different sections), then sequential validation
T004 + T006 + T007  →  T008  →  T009  →  T010
T005 can parallel with T006 + T007

# Phase 6 — smoke test and memory check in parallel after T028 passes
T027  →  T028  →  T029 + T030
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (read realm-export structure)
2. Complete Phase 2: Foundational (edit + restart Keycloak) — **CRITICAL**
3. Complete Phase 3: User Story 1 (enrolment flow)
4. **STOP and VALIDATE**: Enrolled user count == 1, required action cleared
5. Proceed to US2 and US3 only after US1 checkpoint passes

### Incremental Delivery

1. Phase 1 + 2 → Keycloak running with browser-mfa flow (foundation)
2. Phase 3 (US1) → Enrolment works → first falsifiable evidence
3. Phase 4 (US2) → Challenge works for enrolled users
4. Phase 5 (US3) → Bypass blocked, lockout confirmed
5. Phase 6 → Smoke tests automated, docs updated

---

## Notes

- All acceptance tests use `curl` commands with exact expected values — no "looks good" criteria
- The test user `mfa-test-enrol` is created and cleaned up within the verification tasks; it does not need to persist in the realm export
- If `make restart svc=keycloak` does not trigger a fresh realm import, run `make down && make up-auth` to force a full re-import — realm imports only run on first Keycloak startup against an empty database
- If Keycloak already has the `inference-platform` realm from feature 024 (non-empty DB), use the Keycloak Admin REST API to apply changes directly, then export the updated realm and overwrite `services/keycloak/realm-export.json`
