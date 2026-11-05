.PHONY: demo test lint e2e hooks

demo:
	mkdir -p /var/lib/trustscout
	test -f .env || cp .env.example .env
	docker compose up --build

test:
	python3 -m pytest -q

lint:
	python3 -m ruff check .
	python3 -m ruff format --check .

e2e:
	TRUSTSCOUT_E2E=1 python3 -m pytest -q -m e2e

hooks:
	python3 -m pip install pre-commit
	pre-commit install
