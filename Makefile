.PHONY: install up down status init states sync import process run refresh clean-results
INPUT ?= addresses_sample.csv
OUTPUT ?= data/output/coverage_results.csv
AS_OF ?=
PROVIDERS ?= att,tmo,vzw
PYTHON ?= .venv/bin/python

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	cp -n .env.example .env || true
	docker compose up -d
	$(PYTHON) init_database.py

up:
	docker compose up -d

down:
	docker compose down

status:
	docker compose ps

init:
	$(PYTHON) init_database.py

states:
	$(PYTHON) -c "from pathlib import Path; from extract_states import extract_states; print(*extract_states(Path('$(INPUT)')), sep='\n')"

sync:
	$(PYTHON) sync_fcc_data.py --input $(INPUT) --providers $(PROVIDERS) $(if $(AS_OF),--as-of $(AS_OF),)

run refresh:
	$(PYTHON) run_pipeline.py --input $(INPUT) --output $(OUTPUT) --providers $(PROVIDERS) $(if $(AS_OF),--as-of $(AS_OF),)

process:
	$(PYTHON) process_addresses.py --input $(INPUT) --output $(OUTPUT) --release-id $$(cat data/catalog/selected_release.txt)

clean-results:
	rm -f data/output/*.csv
