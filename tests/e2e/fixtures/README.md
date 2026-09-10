# E2E LLM fixtures (Phase 4 Step 4.1)

Each file is a `RAGPIPE_FAKE_LLM` fixture consumed by
`src/backend/fake_llm.py::FakeLLMFixture`. The e2e harness
(`desktop/e2e/support/harness.ts`) points the spawned backend at
`tests/e2e/fixtures/<name>.json` via `test.use({ fakeLlmFixture: "<name>" })`.

Format:

```json
{
  "rules": [
    { "when": { "any": ["substring in the prompt"] }, "respond": "<verbatim completion>" }
  ],
  "default": "<used when no rule matches>"
}
```

The combined `system_prompt + "\n" + user_prompt` (lower-cased) is tested against
each rule in order; first match wins. `respond` is returned as the raw completion
— the caller (`RetrievalAgent`, `SlotExtractor`, `GenerationOrchestrator`,
`GroundingValidator`) does its own JSON recovery.
