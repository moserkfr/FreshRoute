# FreshRoute

Explainable, shelf-life-aware distribution planning for perishable produce distributors in India
(PS03).

## Setup

```bash
python tasks.py setup        # or: make setup   (creates .venv, installs package + dev tools)
python tasks.py test         # or: make test
```

Every Makefile target is mirrored by `python tasks.py <target>` for machines without `make`:
`setup, fit, gen, test, eval, api, web, lint, schemas`. Pass extra arguments after the target
(`python tasks.py eval --hidden`) or with `make eval ARGS=--hidden`.

## Configuration

All parameters live in `config/*.yaml`, each real-world number with a `source`. Validate with
`python -m freshroute.config`.

## Status

- [x] M0 skeleton: configs, schemas, task runner, tests
- [ ] M1 shelf life, generator, simulator, B1 baseline, metrics
- [ ] M2 market model
- [ ] M3 planner (filter, MILP, discount, validator, explain) + B2/B3
- [ ] M4 scenarios, Monte Carlo, sensitivity, replanning
- [ ] M5 API
- [ ] M6 frontend
- [ ] M7 hidden-set evaluation and polish
