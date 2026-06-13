package inference

import future.keywords.in

# ── Input contract ────────────────────────────────────────────────────────────
# Kong or Guardrails calls OPA at: POST /v1/data/inference/allow
# Expected input document:
#   {
#     "method": "POST",
#     "path":   "/v1/chat/completions",
#     "roles":  ["engineer"],          # from X-User-Roles header (JSON array)
#     "team":   "ml-platform"          # from X-User-Team header
#   }

# ── Role helpers ──────────────────────────────────────────────────────────────

is_admin   if "admin"    in input.roles
is_engineer if "engineer" in input.roles
is_viewer  if "viewer"   in input.roles

# ── Path matchers ─────────────────────────────────────────────────────────────

is_inference_path    if startswith(input.path, "/v1/chat")
is_inference_path    if startswith(input.path, "/v1/completions")
is_embeddings_path   if startswith(input.path, "/v1/embeddings")
is_models_path       if startswith(input.path, "/v1/models")
is_batch_path        if startswith(input.path, "/v1/batch")
is_cache_flush_path  if startswith(input.path, "/cache/flush")
is_spend_path        if startswith(input.path, "/v1/spend")

# ── Default deny ──────────────────────────────────────────────────────────────

default allow := false

# ── Allow rules ───────────────────────────────────────────────────────────────

# Admin: unrestricted access to every path and method
allow if is_admin

# Engineer: inference, embeddings, models catalogue, batch, read-only spend
allow if {
    is_engineer
    not is_cache_flush_path
}

# Viewer: read-only model catalogue only; no inference
allow if {
    is_viewer
    is_models_path
    input.method == "GET"
}

# ── Deny explanations (used in error responses) ───────────────────────────────

deny_reason := "cache_flush_requires_admin" if {
    is_cache_flush_path
    not is_admin
}

deny_reason := "inference_requires_engineer_or_admin" if {
    is_inference_path
    not is_admin
    not is_engineer
}

deny_reason := "embeddings_requires_engineer_or_admin" if {
    is_embeddings_path
    not is_admin
    not is_engineer
}

deny_reason := "spend_requires_engineer_or_admin" if {
    is_spend_path
    not is_admin
    not is_engineer
}

deny_reason := "write_requires_engineer_or_admin" if {
    input.method != "GET"
    not is_admin
    not is_engineer
}

deny_reason := "insufficient_role" if {
    not is_admin
    not is_engineer
    not is_viewer
}
