"""Dynamic model management (Phase 1 Step 1.6).

The Personal AI Companion ships with zero generative models. The Tauri shell
starts a bundled Ollama sidecar; this package is the Python backend's window
onto it — over HTTP only, never the process:

- ``OllamaManager``        — read-only status wrapper (``/api/tags`` etc.).
- ``ModelManager``         — download lifecycle, the three-state interruption
                             guarantee, integrity verification, active-model
                             switching.
- ``ModelInferenceRouter`` — the app-config-authoritative front door for
                             generation (Phase 3 routes through it).
- ``AppConfig``            — the mutable ``active_model`` app-config file.
- ``load_model_catalog``   — the bundled ``config/models/catalog.json``.
"""
