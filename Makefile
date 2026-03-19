.PHONY: format lint typecheck test

format:
	.venv/bin/ruff format .

lint:
	.venv/bin/ruff check .

typecheck:
	.venv/bin/mypy .

test:
	.venv/bin/pytest

