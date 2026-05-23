SHELL := /bin/bash
PYTHON ?= python

.PHONY: help fetch smoke run report regrade category clean-traj

help:
	@echo "OmicOS-BixBench targets"
	@echo "  fetch                              - pull BixBench-Verified-50 from HF"
	@echo "  smoke                              - 1-question sanity check"
	@echo "  run RID=<name> [J=4]               - full 50-question sweep"
	@echo "  report RID=<name>                  - regenerate report.md + grades.csv"
	@echo "  regrade RID=<name>                 - re-grade answers in place"
	@echo "  category                           - per-category Pass@1 bar chart"
	@echo "  clean-traj RID=<name>              - remove results/<run>/ workspace + traj"

fetch:
	uv run omicos-bixbench fetch

smoke:
	bash scripts/smoke.sh

run:
	@if [ -z "$(RID)" ]; then echo "usage: make run RID=<run_id> [J=<concurrency>]"; exit 1; fi
	uv run omicos-bixbench run --run-id $(RID) -j $${J:-4}

report:
	@if [ -z "$(RID)" ]; then echo "usage: make report RID=<run_id>"; exit 1; fi
	uv run omicos-bixbench report $(RID)

regrade:
	@if [ -z "$(RID)" ]; then echo "usage: make regrade RID=<run_id>"; exit 1; fi
	uv run omicos-bixbench regrade $(RID)

category:
	$(PYTHON) analysis/category_breakdown.py

clean-traj:
	@if [ -z "$(RID)" ]; then echo "usage: make clean-traj RID=<run_id>"; exit 1; fi
	find results/$(RID) -type d -name workspace -prune -exec rm -rf {} +
	find results/$(RID) -name 'trajectory.jsonl' -delete
