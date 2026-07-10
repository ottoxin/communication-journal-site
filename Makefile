PYTHON := .venv/bin/python
CLI := .venv/bin/communication-journal-site
DATE ?= $(shell date +%F)
OPENALEX ?= 1

ifeq ($(OPENALEX),0)
OPENALEX_FLAG := --no-openalex
else
OPENALEX_FLAG :=
endif

.PHONY: setup test check update update-strict serve

setup:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

check: test
	$(PYTHON) -m compileall -q src
	git diff --check

update:
	$(CLI) run-weekly --digest-date $(DATE) $(OPENALEX_FLAG)

update-strict:
	$(CLI) run-weekly --digest-date $(DATE) $(OPENALEX_FLAG) --strict

serve:
	$(PYTHON) -m http.server 8000 --directory site
