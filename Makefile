PY ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
SCENARIO ?= scenarios/public_01_dev.yaml
NAME ?= $(basename $(notdir $(SCENARIO)))

baseline:
	$(PY) -m sim baseline --scenario $(SCENARIO) --run-dir runs/base_$(NAME)
serve:
	$(PY) -m sim serve --scenario $(SCENARIO) --run-dir runs/mine_$(NAME) --force
grade:
	mkdir -p reports && python -m sim grade --run runs/mine_$(NAME) --baseline runs/base_$(NAME) --out reports/$(NAME).md
test:
	$(PY) -m pytest -q tests
check:
	$(PY) -m pytest -q -p no:warnings tests
run:
	./scripts/run_scenario.sh $(NAME)
