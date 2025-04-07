PROJECT_MODULE:=pyavd_cli

install-deps: ## Install required dependencies
ifeq (, $(shell which poetry))
	$(error "No poetry in $(PATH), see https://python-poetry.org/docs/#installation to install it in your system")
endif
	poetry config virtualenvs.in-project true
	poetry sync --all-extras

ruff:
	poetry run ruff check .

format:
	poetry run ruff format

mypy:
	poetry run mypy $(PROJECT_MODULE)

build:
	poetry run python -m build

pre-commits:
	poetry run pre-commit install

# Make sure the version is updated before publishing!
ci-publish:
	poetry config repositories.gitlab ${CI_SERVER_URL}/api/v4/projects/${CI_PROJECT_ID}/packages/pypi
	poetry config certificates.gitlab.cert false
	poetry publish --build --repository gitlab -u gitlab-ci-token -p ${CI_JOB_TOKEN}

PHONY: install-deps ruff format build unit mypy pre-commits ci-publish version-patch
