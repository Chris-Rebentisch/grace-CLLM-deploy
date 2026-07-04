"""Elicitation Protocol telemetry ingest + models (Chunk 27, D195).

This package holds the append-only telemetry writer and the Pydantic
models for the event envelope defined in the Elicitation Protocol §8.2.
The scope is deliberately minimal per D195: ingest + validation +
append-only write. Aggregation, signal computation, and alerting are
deferred to Chunk 32.
"""
