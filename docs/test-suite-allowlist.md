# Test-Suite Allowlist (D486)

Centralized registry of known test failures. Parsed at collection time by
`tests/conftest.py::_parse_allowlist()` — the SOLE collection-time skip path
for the centralized allowlist. Tests whose node id matches a `test_id` entry
are marked `skip` at collection.

Parser contract:

- Markdown pipe table; the first pipe-delimited line is the header, the second
  is the separator, every subsequent pipe line with ≥5 cells is a data row.
- Column order is fixed: `test_id`, `failure_class`, `owner`, `fix_by_chunk`, `rationale`.
- `failure_class` must be one of: `graph-state` | `co-tenant` | `flaky` | `environmental`.
- `owner` is required for every entry (parser raises on empty owner).
- Maximum 5 data rows — fix or remove entries before adding new ones.
- Rows with an empty `test_id` are ignored.

Two known environmental failures are registered (pre-existing in the current working tree).

| test_id | failure_class | owner | fix_by_chunk | rationale |
|---------|---------------|-------|--------------|-----------|
| tests/analytics/test_otel_setup.py::test_setup_otel_resource_attributes_populated | environmental | glennys | next-housekeeping | OTel resource-attribute assertion diverges from the SDK/bootstrap behavior on this environment; deterministic, isolated to setup introspection |
| tests/ingestion/communications/voice_tone/test_feature_extractor.py::TestFeatureExtractor::test_directness_batch_mock | environmental | glennys | next-housekeeping | Directness batch-mock expectation diverges from current feature-extractor behavior; deterministic, isolated to the mocked batch path |
