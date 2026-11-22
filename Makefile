PYTHON ?= python3
export PYTHONPATH := src:.

.PHONY: check report demo serve site analysis

check:
	$(PYTHON) -m space_team_genesis.cli check

report:
	$(PYTHON) -m space_team_genesis.cli report --split all

demo:
	$(PYTHON) -m space_team_genesis.cli run --seed 2013 --profile dev --size small --mode closed --policy reconcile --out outputs/trajectories.jsonl

serve:
	$(PYTHON) -m space_team_genesis.cli serve

# Site + API on one origin, open mode: the sandbox shows the terminal reward (demo only).
site:
	$(PYTHON) -m space_team_genesis.cli serve --mode open

# Measured failure analysis per issue type; also rewrites the public trajectories/.
analysis:
	$(PYTHON) -m auditgym.analysis --trajectories trajectories
