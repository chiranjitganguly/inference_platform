-- AI Inference Platform — PostgreSQL database provisioning
--
-- This script runs automatically on the first start of the postgres service
-- when the pg_data volume is empty. On subsequent starts the init directory
-- is skipped by the postgres entrypoint, making restarts fully idempotent.
--
-- Databases provisioned:
--   litellm   — LiteLLM Proxy spend logs, virtual keys, model config
--   keycloak  — Keycloak users, realms, sessions
--   mlflow    — MLflow model registry, experiments, runs
--   kong      — Kong services, routes, consumers, plugins
--   phoenix   — Phoenix Arize LLM traces and spans (Phase 06)
--   langfuse  — Langfuse prompt versions, evaluations, traces (Phase 06)
--
-- Each CREATE DATABASE uses a NOT EXISTS guard so the statement is safe
-- to re-execute (belt-and-suspenders against partial init recovery).
-- GRANT ALL PRIVILEGES uses CURRENT_USER so the script is portable across
-- environments with different POSTGRES_USER values.

-- ── Create databases ────────────────────────────────────────────────────────

SELECT format('CREATE DATABASE %I', 'litellm')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm')\gexec

SELECT format('CREATE DATABASE %I', 'keycloak')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec

SELECT format('CREATE DATABASE %I', 'mlflow')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mlflow')\gexec

SELECT format('CREATE DATABASE %I', 'kong')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'kong')\gexec

SELECT format('CREATE DATABASE %I', 'phoenix')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'phoenix')\gexec

SELECT format('CREATE DATABASE %I', 'langfuse')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec

-- ── Grant privileges ─────────────────────────────────────────────────────────

GRANT ALL PRIVILEGES ON DATABASE litellm  TO CURRENT_USER;
GRANT ALL PRIVILEGES ON DATABASE keycloak TO CURRENT_USER;
GRANT ALL PRIVILEGES ON DATABASE mlflow   TO CURRENT_USER;
GRANT ALL PRIVILEGES ON DATABASE kong     TO CURRENT_USER;
GRANT ALL PRIVILEGES ON DATABASE phoenix  TO CURRENT_USER;
GRANT ALL PRIVILEGES ON DATABASE langfuse TO CURRENT_USER;
