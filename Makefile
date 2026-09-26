# Every uv command here reads only ./uv.toml, never a personal uv config (it would leak into uv.lock).
export UV_CONFIG_FILE := $(CURDIR)/uv.toml

.PHONY: help check lint docs build clean release

help:
	@echo "make check     what CI runs: lint, lock files, docs coverage and schema, recipe lint, docs build and links"
	@echo "make lint      ruff, the version CI pins (make lint FIX=1 applies safe fixes)"
	@echo "make docs      preview the docs site at http://localhost:3000"
	@echo "make build     build the wheel and sdist into dist/ and check their metadata"
	@echo "make clean     remove build artifacts and caches"
	@echo "make release   how to publish (a version tag; CI does the rest)"

check: lint
	! grep -n 'exclude-newer-package' uv.lock server/uv.lock
	uv run docs-site/check.py
	uv run hunch lint src/hunch/recipes/agent_commands
	uv run hunch lint src/hunch/recipes/tickets
	uv run hunch lint src/hunch/recipes/rag_answers
	cd docs-site && npx -y mint@latest validate
	cd docs-site && npx -y mint@latest broken-links --check-anchors

lint:
	uvx ruff@0.16.8 check $(if $(FIX),--fix) .

docs:
	cd docs-site && npx -y mint@latest dev

build: clean
	uv build
	uvx twine check --strict dist/*

clean:
	rm -rf dist/ build/ *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# Publishing happens in CI only (.github/workflows/release.yml, PyPI trusted publishing).
release:
	@echo "Version in src/hunch/__init__.py: $$(uv run --no-project python -c 'import sys; sys.path.insert(0, "src"); import hunch; print(hunch.__version__)')"
	@echo "Tag the release commit on main and push the tag:"
	@echo "  git tag v<version> && git push origin v<version>"
