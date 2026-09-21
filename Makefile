PYTHON ?= .venv/bin/python

.PHONY: help setup test integration demo inspect db-check
help:
	@echo 'Targets: setup, test, integration (requires PostgreSQL), demo, inspect, db-check'
setup:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r requirements.txt
test:
	$(PYTHON) -m unittest discover -s tests -v
integration:
	TELEMETRY_INTEGRATION=1 $(PYTHON) -m unittest discover -s tests -v
demo:
	$(PYTHON) -m telemetry demo
inspect:
	$(PYTHON) -m telemetry inspect fixtures/readings.csv
db-check:
	$(PYTHON) -m telemetry check-db
