# Contributing

Issues and pull requests are welcome. Outline changes its API and its markdown output between versions, so reports of a document that margin cannot patch are very useful. Please include your Outline version and, if you can, the document's markdown before and after.

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Please add a test for every change in behaviour. The patch planner has a randomised test, and a change to it must keep that test passing.
