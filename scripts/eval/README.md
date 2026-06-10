# Eval harness

Regression net for the LLM pipeline. Run it **before** changing a model, the
system prompt, or refactoring `pipeline.py`, and again after — a green run means
behavior held. This is what makes those changes safe to ship.

## How it works

1. Sends each `question` from a cases file through the **real tenant API**
   (full pipeline + real LLM).
2. Reads the resulting `llm_request_logs` row for that chat (on-box, via the
   app's DB session) to inspect tier, tool calls, latency.
3. Evaluates the case's assertions and prints a pass/fail report.
   Exit code is non-zero if any case fails (CI-friendly).

## Run

```bash
cd /home/ai-platform
PYTHONPATH=backend backend/venv/bin/python3 scripts/eval/run_eval.py \
    --base-url http://127.0.0.1:8000 \
    --tenant <TENANT_ID> --api-key <RAW_API_KEY> \
    --cases scripts/eval/cases.example.yaml \
    --out scripts/eval/report.md
```

Point it at a **configured** tenant (e.g. IT-Invest) so cases can exercise that
tenant's real tools and KB. Keep a private cases file per tenant; the raw API
key is only shown once at key creation — store it in your password manager.

## Assertions

See the header of `run_eval.py` and `cases.example.yaml` for the full list:
`content_contains`, `content_not_contains`, `content_regex`, `lang` (ru/uk/en),
`served_by` (tier0_template/llm), `tool_called`, `min/max_tool_calls`,
`expect_tool`, `max_latency_ms`.

## Notes / limits

- `lang` is a Cyrillic-majority heuristic (reuses the pipeline's detector):
  answers heavy with English technical terms may read as `en`. Use
  `content_contains` for stricter language checks when needed.
- `expect_tool` is a substring match against the request log, not a structural
  assertion — good enough to catch "the wrong tool / no tool" regressions.
- Each case runs in a fresh chat (no cross-contamination), but cases share the
  tenant — don't write cases that mutate tenant state.
