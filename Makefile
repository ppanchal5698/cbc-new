# Single entry point for every local task (§8.5).
#
# Everything runs inside the compose stack so a developer machine needs Docker
# and nothing else. `make help` lists the targets.

.DEFAULT_GOAL := help
.PHONY: help up down logs migrate seed test test-fast eval calibrate cost-report \
        eval-check eval-baseline lint format types schema shell dbshell \
        bedrock-resolve aws-whoami \
        plan-dev apply-dev destroy-dev fmt-tf clean

COMPOSE := docker compose
API     := $(COMPOSE) exec -T api
PIPE    := $(COMPOSE) exec -T pipeline
# pytest honours `testpaths` only when invoked FROM the rootdir. The api service
# works out of /app/api, so running the suite there silently collected api/ alone
# and skipped every test under backend/tests/ — including the two traceability
# tests that are the whole point of the suite. Run it from /app.
PYTEST  := $(COMPOSE) exec -T -w /app api pytest
# Same reason: linting from /app/api leaves pipeline/, shared/ and tests/ unchecked.
RUFF    := $(COMPOSE) exec -T -w /app api ruff
WEB     := $(COMPOSE) exec -T frontend

help:  ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── stack ────────────────────────────────────────────────────────────────────

up:  ## Build and start postgres, ministack, api, pipeline, frontend
	$(COMPOSE) up --build -d
	@echo "waiting for health..."
	@$(COMPOSE) ps

down:  ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

logs:  ## Tail every service
	$(COMPOSE) logs -f --tail=100

clean:  ## Stop the stack and DELETE the database volume
	$(COMPOSE) down -v

# ── database ─────────────────────────────────────────────────────────────────

migrate:  ## Apply Django migrations (Django owns every migration — ADR-0001)
	$(API) python manage.py migrate --noinput

seed:  ## Seed reference data: finish codes, throat depths, margin bands, multipliers, tax rates
	$(API) python manage.py seed_reference --with-sample-catalog

dbshell:  ## psql into the dev database
	$(COMPOSE) exec postgres psql -U postgres -d cbccopilot

shell:  ## Django shell
	$(COMPOSE) exec api python manage.py shell

# ── quality ──────────────────────────────────────────────────────────────────

test:  ## Full backend suite, including schema parity
	$(PYTEST) -q

test-fast:  ## Skip anything marked integration
	$(PYTEST) -q -m "not integration"

eval:  ## Golden-set evaluation; prints PER-FIELD metrics incl. absent-accuracy (§5.10)
	$(PIPE) python -m tests.golden.run_eval

eval-check:  ## ...and exit non-zero on any regression against the recorded baseline
	$(PIPE) python -m tests.golden.run_eval --check

eval-baseline:  ## Record the current numbers as the baseline. REVIEW THEM before committing.
	$(PIPE) python -m tests.golden.run_eval --write-baseline

calibrate:  ## Per-field confidence threshold curve for CBC to pick an operating point (§5.9)
	$(PIPE) python ops/scripts/calibrate_threshold.py

cost-report:  ## Per-bid-set AWS cost attribution from live tables (§10.3)
	$(PIPE) python ops/scripts/cost_report.py

lint:  ## ruff across the backend; tsc and eslint across the frontend
	$(RUFF) check .
	$(WEB) npx tsc --noEmit
	$(WEB) npm run lint

format:  ## Apply ruff autofixes
	$(RUFF) check --fix .

# ── API contract ─────────────────────────────────────────────────────────────

schema:  ## Write backend/schema.yml from the live API
	$(API) python manage.py spectacular --file /app/schema.yml
	@echo "wrote backend/schema.yml"

types: schema  ## Regenerate frontend types from the OpenAPI schema (fixes H2)
	@if [ -d frontend ]; then \
	  cd frontend && npx openapi-typescript ../backend/schema.yml -o src/lib/types.generated.ts; \
	else \
	  echo "no frontend/ in this repo — schema.yml is the contract for whoever builds it"; \
	fi

# ── AWS ──────────────────────────────────────────────────────────────────────

aws-whoami:  ## Confirm which AWS account the CLI is pointed at
	aws sts get-caller-identity

bedrock-resolve:  ## Resolve Bedrock model IDs at deploy time and pin them in SSM (C5)
	python ops/scripts/resolve_bedrock_models.py

plan-dev:  ## terraform plan for the free-tier dev environment
	cd infra/envs/dev && terraform init -input=false && terraform plan -input=false

apply-dev:  ## terraform apply for dev — REVIEW THE PLAN FIRST
	cd infra/envs/dev && terraform apply -input=false

destroy-dev:  ## Tear the dev environment down
	cd infra/envs/dev && terraform destroy -input=false

fmt-tf:  ## terraform fmt across the infra tree
	cd infra && terraform fmt -recursive
