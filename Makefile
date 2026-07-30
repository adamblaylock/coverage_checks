PYTHON := .venv/bin/python
PIP    := .venv/bin/pip

.PHONY: install up down init run sync states clean

install: up
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(MAKE) init

up:
	docker compose up -d
	@echo "Waiting for PostGIS to be ready..."
	@sleep 5

down:
	docker compose down

init:
	$(PYTHON) -c "from db import init_db; init_db()"

run:
	$(MAKE) up
	$(MAKE) init
	$(PYTHON) run_pipeline.py \
		--input $(INPUT) \
		--output $(if $(OUTPUT),$(OUTPUT),data/output/coverage_results.csv) \
		$(if $(AS_OF),--as-of $(AS_OF),)

sync:
	$(MAKE) up
	$(MAKE) init
	$(PYTHON) run_pipeline.py \
		--input $(INPUT) \
		--output /dev/null \
		--sync-only

states:
	$(PYTHON) run_pipeline.py --input $(INPUT) --states-only

clean:
	rm -rf data/downloads/* data/coverage/* data/catalog/* data/output/*
	touch data/downloads/.gitkeep data/coverage/.gitkeep \
	      data/catalog/.gitkeep data/output/.gitkeep
