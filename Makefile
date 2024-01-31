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

# Make sure the version is updated before publishing!
ci-publish:
	poetry config repositories.gitlab ${CI_SERVER_URL}/api/v4/projects/${CI_PROJECT_ID}/packages/pypi
	poetry config certificates.gitlab.cert false
	poetry publish --build --repository gitlab -u gitlab-ci-token -p ${CI_JOB_TOKEN}

PHONY: install-deps build mypy pre-commits ci-publish
