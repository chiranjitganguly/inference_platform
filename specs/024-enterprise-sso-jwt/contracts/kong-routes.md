# Contract: Kong Route Authentication

**Feature**: `024-enterprise-sso-jwt` | **Version**: 1.0 | **Date**: 2026-06-09

This contract documents the Kong route and plugin changes introduced by this feature. All routes use the built-in `jwt` plugin. The `key-auth` plugin is removed from all `/v1/*` routes.

---

## Plugin Replacement Summary

| Route | Before (Phase 012) | After (Phase 024) |
|---|---|---|
| `litellm-proxy` `/v1` | `key-auth` | `jwt` + `post-function` |
| `models-catalogue` `/v1/models` | `key-auth` | `jwt` |
| `litellm-embeddings-route` `/v1/embeddings` | `key-auth` | `jwt` + `post-function` |
| `cache-flush` `/cache/flush` | `key-auth` | `jwt` |
| `spend-report` `/v1/spend` | none | `jwt` |
| `phoenix-ui-route` `/phoenix` | *(new route)* | `jwt` (audience: `phoenix-ui`) |
| `langfuse-ui-route` `/langfuse` | *(new route)* | `jwt` (audience: `langfuse-ui`) |
| `litellm-health` `/health` | none | none (exempt, FR-014) |

---

## JWT Plugin Configuration

Applied as a **global** plugin (applies to all routes) with per-route audience overrides where needed:

```yaml
plugins:
  - name: jwt
    config:
      key_claim_name: iss
      claims_to_verify:
        - exp
        - nbf
      clock_skew: 30
      anonymous: ~
      header_names:
        - Authorization
      uri_param_names: []
      cookie_names: []
      run_on_preflight: true
      maximum_expiration: 0
```

Phoenix and Langfuse routes use route-scoped jwt plugin config with `key_claim_name: aud` to enforce audience isolation (separate Kong consumers per UI service).

---

## Post-Function Plugin Configuration

Applied on inference routes (`litellm-proxy`, `litellm-embeddings-route`):

```yaml
- name: post-function
  config:
    access:
      - |
        local auth = kong.request.get_header("Authorization")
        if auth and auth:sub(1,7):lower() == "bearer " then
          local token = auth:sub(8)
          local segments = {}
          for seg in token:gmatch("[^.]+") do
            segments[#segments+1] = seg
          end
          if #segments == 3 then
            local b64 = segments[2]:gsub("-","+"):gsub("_","/")
            local pad = (4 - #b64 % 4) % 4
            b64 = b64 .. string.rep("=", pad)
            local decoded = ngx.decode_base64(b64)
            if decoded then
              local ok, claims = pcall(require("cjson").decode, decoded)
              if ok and claims then
                if claims.roles then
                  kong.service.request.set_header("X-User-Roles",
                    require("cjson").encode(claims.roles))
                end
                if claims.team then
                  kong.service.request.set_header("X-User-Team", claims.team)
                end
                if claims.sub then
                  kong.service.request.set_header("X-User-Sub", claims.sub)
                end
              end
            end
          end
        end
```

---

## New Services (Phoenix and Langfuse)

```yaml
services:
  - name: phoenix-ui
    url: http://arize-phoenix:6006
    connect_timeout: 5000
    read_timeout: 30000
    write_timeout: 30000
    routes:
      - name: phoenix-ui-route
        paths:
          - /phoenix
        strip_path: true
        plugins:
          - name: jwt
            config:
              key_claim_name: aud      # match consumer by aud value "phoenix-ui"
              claims_to_verify: [exp, nbf]
              clock_skew: 30

  - name: langfuse-ui
    url: http://langfuse-server:3002
    connect_timeout: 5000
    read_timeout: 30000
    write_timeout: 30000
    routes:
      - name: langfuse-ui-route
        paths:
          - /langfuse
        strip_path: true
        plugins:
          - name: jwt
            config:
              key_claim_name: aud      # match consumer by aud value "langfuse-ui"
              claims_to_verify: [exp, nbf]
              clock_skew: 30
```

---

## Kong Consumers

```yaml
consumers:
  - username: keycloak-realm         # API routes (iss = realm URL)
  - username: phoenix-realm          # Phoenix UI route (aud = phoenix-ui)
  - username: langfuse-realm         # Langfuse UI route (aud = langfuse-ui)
  - username: smoke-test-consumer    # Existing smoke test consumer (retained)
```

JWT credentials for `phoenix-realm` and `langfuse-realm` share the same `rsa_public_key` as `keycloak-realm` but use `key: "phoenix-ui"` and `key: "langfuse-ui"` respectively to match the `aud` claim via `key_claim_name: aud`.
