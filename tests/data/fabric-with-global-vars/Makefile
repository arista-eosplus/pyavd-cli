### Generic Variables
SHELL := /bin/bash
INVENTORY_PATH?=inventory
AVD_VERSION?=5.3.0
LIMIT_ARGS?=
PYTHON?=$(shell which python3)
VENV_PATH?=.venv
VENV_BIN_PATH:=$(VENV_PATH)/bin
VENV_PYTHON:=$(VENV_BIN_PATH)/python
VENV_PYAVD_VERSION:=$(VENV_PYTHON) -c 'from importlib.metadata import version; print(version("pyavd").replace(".dev", "-dev"))'
ROOT_DIR:=$(dir $(realpath $(lastword $(MAKEFILE_LIST))))
DIST_PATH?=$(realpath $(ROOT_DIR)dist)
LAST_SNAPSHOT=$(lastword $(sort $(wildcard $(INVENTORY_PATH)/intended-*)))

REQUIREMENTS:=requirements.txt
COLLECTION_REQUIREMENTS:=collection-requirements.yml

ifneq (,$(wildcard ./.env))
	include .env
	export
endif

.PHONY: help
help: ## Display help message (*: main entry points / []: part of an entry point)
	@grep -hE '^[0-9a-zA-Z_-]+\.*[0-9a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

.PHONY: build
build: snapshot ## Run ansible playbook to build fabric configuration
	$(VENV_BIN_PATH)/ansible-playbook playbooks/build.yml -i $(INVENTORY_PATH)/inventory.yml $(LIMIT_ARGS)

.PHONY: build-pyavd
build-pyavd: snapshot ## Run pyavd build
	$(VENV_BIN_PATH)/pyavd-build -i $(INVENTORY_PATH)/inventory.yml -o $(INVENTORY_PATH)/intended -f FABRIC $(LIMIT_ARGS) -v

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV_PATH)
	$(VENV_PYTHON) -m pip install --upgrade pip pip-tools

$(REQUIREMENTS): $(VENV_PYTHON) pyproject.toml
	$(VENV_BIN_PATH)/pip-compile -o requirements.txt pyproject.toml

.PHONY: install-deps
install-deps: $(VENV_PYTHON) $(REQUIREMENTS) $(COLLECTION_REQUIREMENTS) ## Install required dependencies
	$(VENV_BIN_PATH)/pip-sync
	$(VENV_BIN_PATH)/ansible-galaxy collection install arista.avd:==$$($(VENV_PYAVD_VERSION))
	$(VENV_BIN_PATH)/ansible-galaxy install -r $(COLLECTION_REQUIREMENTS)

.PHONY: snapshot
snapshot: ## Make a snapshot of current $(INVENTORY_PATH)/intended
	[ -d $(INVENTORY_PATH)/intended ] && mv $(INVENTORY_PATH)/intended $(INVENTORY_PATH)/intended-$$(date -r $(INVENTORY_PATH)/intended +%Y%m%d%H%M%S) || exit 0

.PHONY: show-diff
show-diff: ## Diff between current build and last build
ifeq ($(LAST_SNAPSHOT),)
	@echo "No snapshots found. Build the fabric first."
else
	diff --color -ry --suppress-common-lines $(LAST_SNAPSHOT) $(INVENTORY_PATH)/intended && echo "No differences found" || exit 0
endif

.PHONY: freeze
freeze: ## Freeze requirements
	$(VENV_BIN_PATH)/pip-compile -o requirements.txt pyproject.toml

.PHONY: pip-compile-upgrade
pip-compile-upgrade: ## Upgrade requirements
	$(VENV_BIN_PATH)/pip-compile -o requirements.txt pyproject.toml --upgrade

ifndef NO_AUTO_INSTALL_DEPS
build: install-deps
build-skip-doc: install-deps
build-pyavd: install-deps
cv-deploy: install-deps
endif
