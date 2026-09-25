.PHONY: dev check integration down

dev:
	python3 scripts/dev-secrets.py
	docker compose up -d --build --wait

check:
	python3 -m ruff check management scripts tests
	python3 -m ruff format --check management scripts tests
	python3 -m pytest -q
	python3 -m openapi_spec_validator openapi.yaml
	docker compose config --quiet

integration:
	python3 scripts/dev-secrets.py
	python3 tests/integration.py

down:
	docker compose down
