PROJECT_MODULE:=pyavd_cli

pylint:
	poetry run pylint $(PROJECT_MODULE) || poetry run pylint-exit -efail -wfail $$?

mypy:
	poetry run mypy $(PROJECT_MODULE)

flake8:
	poetry run flake8 $(PROJECT_MODULE)

format:
	poetry run black $(PROJECT_MODULE)
	poetry run isort $(PROJECT_MODULE)

install-deps: ## Install required dependencies
ifeq (, $(shell which poetry))
	$(error "No poetry in $(PATH), see https://python-poetry.org/docs/#installation to install it in your system")
endif
	poetry config virtualenvs.in-project true
	poetry install

build:
	poetry build

pre-commits:
	poetry run pre-commit install

PHONY: install-deps build mypy pre-commits
