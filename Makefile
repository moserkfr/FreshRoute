# FreshRoute — every target delegates to tasks.py so make / no-make behave identically.
# Pass extra arguments with ARGS, e.g.  make eval ARGS=--hidden
PY ?= python
ARGS ?=

.PHONY: setup fit gen test eval api web lint schemas

setup:
	$(PY) tasks.py setup $(ARGS)

fit:
	$(PY) tasks.py fit $(ARGS)

gen:
	$(PY) tasks.py gen $(ARGS)

test:
	$(PY) tasks.py test $(ARGS)

eval:
	$(PY) tasks.py eval $(ARGS)

api:
	$(PY) tasks.py api $(ARGS)

web:
	$(PY) tasks.py web $(ARGS)

lint:
	$(PY) tasks.py lint $(ARGS)

schemas:
	$(PY) tasks.py schemas $(ARGS)
