# AI Inference Platform — Developer Makefile
#
# All Docker Compose operations go through $(COMPOSE) so --env-file is always applied.
# Never invoke docker compose directly — always use make targets.

COMPOSE := docker compose --env-file .env

.DEFAULT_GOAL := help

.PHONY: help \
        up-core up-obs up-auth up-safety up-gov up-portal up-all \
        down down-v \
        restart logs \
        ps stats \
        smoke \
        seed-kong seed-vault

# ── Help ────────────────────────────────────────────────────────────────────

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} \
	/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

# ── Startup ─────────────────────────────────────────────────────────────────

up-core: ## Start core profile (LiteLLM, Kong, Redis×2, Postgres, Prometheus, Grafana)
	$(COMPOSE) --profile core up -d

up-obs: ## Start obs profile (Loki, OTel, Alertmanager, Jaeger, Phoenix Arize, Langfuse)
	$(COMPOSE) --profile obs up -d

up-auth: ## Start auth profile (Keycloak, OPA, Vault)
	$(COMPOSE) --profile auth up -d

up-safety: ## Start safety profile (Presidio, LLM Guard, Guardrails)
	$(COMPOSE) --profile safety up -d

up-gov: ## Start gov profile (MLflow)
	$(COMPOSE) --profile gov up -d

up-portal: ## Start portal profile (Swagger UI, Portal backend, Platform UI)
	$(COMPOSE) --profile portal up -d

up-all: ## Start all profiles
	$(COMPOSE) \
		--profile core \
		--profile obs \
		--profile auth \
		--profile safety \
		--profile gov \
		--profile portal \
		up -d

# ── Teardown ─────────────────────────────────────────────────────────────────

down: ## Stop all containers (volumes preserved)
	$(COMPOSE) down

down-v: ## Stop all containers and remove volumes (destructive — re-seed after)
	$(COMPOSE) down -v

# ── Service operations ───────────────────────────────────────────────────────

restart: ## Restart a service: make restart svc=<name>
ifndef svc
	$(error Usage: make restart svc=<service-name>)
endif
	$(COMPOSE) restart $(svc)

logs: ## Tail logs for a service: make logs svc=<name>
ifndef svc
	$(error Usage: make logs svc=<service-name>)
endif
	$(COMPOSE) logs -f $(svc)

# ── Observability ────────────────────────────────────────────────────────────

ps: ## Show running containers with ports and status
	$(COMPOSE) ps

stats: ## Live per-container CPU and memory (Ctrl-C to exit)
	docker stats

# ── Verification ─────────────────────────────────────────────────────────────

smoke: ## Run smoke tests against Kong :8080 (exit 0 = all pass)
	bash scripts/smoke-test.sh

# ── Seeding ──────────────────────────────────────────────────────────────────

seed-kong: ## Seed Kong routes and services (idempotent)
	bash scripts/seed-kong.sh

seed-vault: ## Seed Vault secrets (idempotent)
	bash scripts/seed-vault.sh
