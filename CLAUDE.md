# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Django 5.2 app ("Música Digital") — an ear-training / sight-reading / music-practice platform. Single Django app (`trainer`) holds all models, views, and URLs; there is no DRF/API layer — endpoints are plain Django views returning `JsonResponse`, called from inline `<script>` blocks in the templates. Deployed on PythonAnywhere (see `ALLOWED_HOSTS` in `config/settings.py`).

## Commands

Run all commands from the repo root (where `manage.py` lives).

```bash
python manage.py runserver          # dev server
python manage.py migrate            # apply migrations
python manage.py makemigrations trainer
python manage.py createsuperuser
python manage.py test trainer       # run tests (trainer/tests.py is currently empty)
python manage.py test trainer.tests.SomeTestCase.test_something  # single test
```

Environment variables (all optional except the two orchestration-analyzer ones, which have no dev-safe fallback by design):
- `DJANGO_SECRET_KEY` — falls back to a hardcoded insecure key.
- `DJANGO_DEBUG` — `'True'`/`'False'` string, defaults to `False`.
- `ANTHROPIC_TEST_API_KEY` — required for the AI orchestration-analysis feature (`orquestador_analizar`) to actually call Claude. Note the name: it's *not* the SDK-conventional `ANTHROPIC_API_KEY`. Without it, the view returns `{"error": "Falta la variable de entorno ANTHROPIC_TEST_API_KEY.", "raw_music_data": ...}` instead of an AI critique.
- `SCORE_FILE_ENCRYPTION_KEY` — a Fernet key (see the generation instructions in `config/settings.py`) used to encrypt/decrypt uploaded score files at rest. Deliberately has no default — `trainer/storage.py` raises loudly instead of silently falling back to plaintext if it's missing. Losing this key permanently locks out every previously-uploaded score file; it must never live in the repo or in git.

One-off data seed scripts at the repo root (`seed_pieces.py`, `seed_gamification.py`, `seed_dictado.py`, `seed_solfeo.py`, `seed_recommended.py`, `fix_req.py`) are run directly with `python <script>.py`, not via `manage.py` — each calls `django.setup()` itself. They populate `Game`, `Achievement`, `Piece`, `DailyGoal` fixture data. Read one before running to know what it seeds/overwrites.

## Architecture

### Data model shape (`trainer/models.py`)

Everything lives in one app, roughly four families of models:

1. **Ear-training games** — `Game` (config: slug, recommended accuracy/attempts to mark "completed"), `Score` (per-user-per-game XP/streak/accuracy, `unique_together('user','game')`), `Attempt` (individual answer log used for per-question-type stats).
2. **Gamification** — `UserProfile` (one per user: total XP, level, daily streak, personal records), `Achievement`/`UserAchievement` (unlocked via slug lookups in `check_achievements()` in `views.py`).
3. **Sheet music / practice tracking** — `Piece` (MusicXML text blob for the games), `SheetMusic`/`Collection`/`Favorite` (the "biblioteca"), `StudySession`, `SheetMusicProgress`, `Playlist`/`PlaylistSheet`, `RehearsalConfig`/`RehearsalLog` (BPM ramp practice), `SheetMarker`/`SheetNote`, `MusicalProject`/`ProjectGoal`/`ProjectSection` (longer-term repertoire tracking with per-section mastery status).
4. **MIDI trainer** — `MidiChordStat` (per-user per-chord mastery, auto-flagged `is_mastered`/`is_problematic` from rolling accuracy), `MidiGameSession`.
5. **Orchestration AI feature** — `ScoreAnalysis` (user, name, the uploaded `.mid`/`.midi`/`.musicxml`/`.mxl` in an encrypted `score_file`, a `JSONField` `analysis_data` with the structured critique, an optional self-referencing `version_de` for re-analyzed revisions, an optional `share_token` for public read-only links). `UserProfile.creditos_analisis`/`creditos_bonus` (two independent, admin-assigned credit pools; see below) gate access. See the dedicated subsection below for how the pipeline actually works.

There are no `ModelForm`s or DRF serializers — views read `request.POST`/`json.loads(request.body)` directly and validate ad hoc.

### Views (`trainer/views.py`, ~1850 lines, one flat module)

- Every game view follows the same pattern: `get_object_or_404(Game, slug=...)`, `Score.objects.get_or_create(...)`, optionally `get_game_stats(user, game)` for the "hardest questions" breakdown, then render a dedicated template.
- `record_attempt` (POST, one endpoint shared by all games via `game_slug`) is the central scoring path: logs an `Attempt`, updates `Score` (streak/points/level via `Score.level_info`), updates `UserProfile` (daily streak, XP, `get_user_level_for_xp`), calls `check_achievements`, and returns the new stats as JSON for the frontend to render without a page reload.
- XP/leveling logic is duplicated in two places that must stay in sync conceptually but are independent: `get_user_level_for_xp()` (global user level, flat XP thresholds in `views.py`) and `Score.level_info` (per-game level, different threshold table in `models.py`).
- `orquestador_analizar` and its supporting views are the odd one out — a whole AI-assisted subsystem built on `music21` + the Anthropic API rather than the game-scoring pattern above. See the dedicated **Orchestration AI feature** subsection below.
- Most mutating endpoints are `@csrf_exempt` + `@login_required` and accept JSON bodies (not Django forms) — POST-only, manual `try/except` returning `{'status': 'error', 'message': str(e)}`.
- Almost every view does local `from .models import X` inside the function body rather than at module scope (existing convention — follow it for new views touching models already imported elsewhere, to avoid circular churn at the top).

### URLs (`trainer/urls.py`)

Flat, no routers/viewsets. Game URLs follow `juego/<slug>/`; most API endpoints are `api/<action>/` or `api/<action>/<id>/`. The orchestration feature's page views live under `orquestador/<action>/` (e.g. `orquestador/historial/`) and its JSON/mutating endpoints under `api/orquestador/<action>/<id>/`. `trainer.urls` is included at the site root in `config/urls.py`, alongside `django.contrib.auth.urls` for login/logout/password views (see `templates/registration/`).

### Frontend

No JS bundler/build step. Per-page logic lives inline in `<script>` blocks inside each `templates/trainer/*.html` file (some are 900+ lines, mixing markup + game logic). Shared pieces:
- `templates/base.html` — nav, global CSS variables, the "Aprendizaje/Evaluación" mode toggle (persisted in `localStorage['app_mode']`, read via `window.getAppMode()`), and the shared theory-explanation modal (`window.showTheoryModal(title, html, callback)`).
- `static/js/teoria.js`, `audio_engine.js`, `pitch_tracker_blueprint.js` — the only shared JS modules (music theory helpers, Tone.js-based audio playback, pitch detection).
- Libraries are loaded via CDN `<script>` tags in `base.html` (Tailwind CDN, Tone.js, Tonal.js); VexFlow is loaded per-template where needed for notation rendering.
- Game pages call the shared `/api/record_attempt/<game_slug>/` endpoint via `fetch` and update their own DOM/state from the JSON response — there's no shared frontend state management.

### Static/media

`STATICFILES_DIRS`/`STATIC_ROOT` and `MEDIA_ROOT` are configured normally; `static/audio/piano/` holds piano note samples served to the browser for the audio engine. `MEDIA_URL`/media serving is only wired up when `DEBUG=True` (see `config/urls.py`) — uploaded `SheetMusic.xml_file` and `ScoreAnalysis.score_file` need a real media-serving setup in production.

### Orchestration AI feature (`orquestador_*`)

An AI-assisted orchestration critique tool: a user uploads a score, `music21` extracts structured data deterministically, Claude (Anthropic API) writes a prose critique constrained to a forced tool schema, and the result is stored, browsable, exportable to PDF, and optionally shareable via a public link. Spans `trainer/views.py` (all `orquestador_*` views plus the deterministic helpers below `ORQUESTACION_TOOL`), `trainer/prompts.py` (the system prompt), `trainer/storage.py` (file encryption), `trainer/services.py` (`consumir_credito_analisis`), and `templates/trainer/orquestador_*.html`. `trainer/orquestador_gemini_legacy.py` is a dead reference file from an earlier Gemini-based prototype — not imported anywhere, kept only for history; `google-genai` is not in `requirements.txt`.

- **Credits gate access, not a paywall check per se**: `UserProfile` has two independent integer pools, `creditos_analisis` (admin-assigned "monthly" allotment) and `creditos_bonus` (never expires). `orquestador_analizar` rejects the request with HTTP 403 if both are `<= 0`. On genuine success, `services.consumir_credito_analisis()` decrements `creditos_analisis` first and only falls through to `creditos_bonus` if that's already 0 — both decrements are conditional `UPDATE ... WHERE creditos_X__gt=0` calls (via `F()`), not read-modify-write in Python, specifically to stay race-safe under concurrent analyses for the same user.
- **Files are encrypted at rest**: `ScoreAnalysis.score_file` uses `trainer.storage.EncryptedFileSystemStorage`, which wraps Django's `FileSystemStorage` to Fernet-encrypt on `_save()` and decrypt on `_open()` (key from the `SCORE_FILE_ENCRYPTION_KEY` env var — see Environment variables above). Callers don't need to know this — `FileField.open()`/`.read()` behave normally. `_parsear_score_descifrado()` (`views.py`) reads the decrypted bytes and parses with `music21` fully in memory (handling `.mxl` zip extraction, `.mid`/`.midi`, and raw `.musicxml`/`.mxl`-unwrapped XML) — the decrypted content is never written to disk, even transiently.
- **Deterministic pre-analysis (no AI) feeds the prompt**: for each part, `calcular_estadisticas_parte` (range, note count, rests, most-frequent pitch class), `evaluar_viabilidad_instrumental` (comfortable-register warnings), and `calcular_densidad_por_compas` (active-instrument count per measure, for the density heatmap) all run before Claude is ever called. `detectar_duplicaciones_verificadas(parts)` is the key one: it walks every pair of parts measure-by-measure and classifies the relationship between their sounding pitches (`_eventos_sonantes_por_compas` extracts one pitch per note-attack per measure, ignoring rests and unpitched percussion; `_clasificar_relacion` compares two same-measure attack sequences) as `unísono`, `octava`, `intervalo_fijo`, or `sin_relacion` — **a mismatched attack count between the two parts is always `sin_relacion`**, doubling requires matching rhythm, not just matching pitches at some point. Contiguous same-classification measures are merged into ranges. This replaced having Claude eyeball doubling from raw pitch data, which it did unreliably.
- **The Claude call**: `client.messages.stream(...)` (model `claude-sonnet-5`, `max_tokens=48000`, forced `tool_choice` on `ORQUESTACION_TOOL`, system prompt = `GUIA_ESTILO_ORQUESTAL` from `trainer/prompts.py` with `cache_control: ephemeral`). The prompt is the JSON-dumped deterministic `analysis_data` (including `duplicaciones_verificadas`, which is sent to Claude but **never merged into the data returned to the frontend** — it's internal-only, feeding the prompt, not the API response). `GUIA_ESTILO_ORQUESTAL` is the single source of truth for output style: it forbids asserting a doubling/unison/octave relationship except when backed by an exact-range entry in `duplicaciones_verificadas`, forbids duplicating content between schema fields (`resumen_general` vs `bloques` vs `resumen_por_instrumento`), and defines the mechanical-edit vocabulary (`transponer_octava`/`silenciar`/free-text with `accion_tipo: null`).
- **Auditing "verificado" claims against the data**: `GUIA_ESTILO_ORQUESTAL` alone can't *guarantee* Claude never writes "verificado"/"verificada" without real backing, so the schema also requires each block to carry `duplicaciones_citadas` (array, may be empty) — the exact `duplicaciones_verificadas` entries backing any such claim in that block's prose. `_auditar_citas_duplicaciones()` (`views.py`) cross-checks this after every analysis, log-only (never blocks or edits the output — the point right now is to collect real cases before deciding whether rejecting/retrying is worth it): it flags either an empty `duplicaciones_citadas` alongside "verificado" language that isn't negated, or a cited entry that doesn't match any real `duplicaciones_verificadas` entry (same unordered part pair, same `tipo`, overlapping measure range). "Isn't negated" is decided by `_hay_negacion_cercana()`, a heuristic that scans the whole clause containing the match (bounded by the nearest `.`/`,`/`;`, since Spanish negation scope is clause-wide rather than a fixed word count) for "sin"/"no" — added specifically to stop flagging legitimately cautious phrasing like "sin que exista una entrada de duplicación verificada".
- **Streaming, not request/response**: the view returns a `StreamingHttpResponse` yielding NDJSON lines — `{"heartbeat": true}` on every stream event (including during the `music21` parsing loop, one per part) plus a final `{"status": "success"/"error", ...}` line. This exists specifically because PythonAnywhere's proxy silently drops long (~100s+) idle connections even though the backend eventually returns 200 — heartbeats keep real bytes flowing the whole time. The stream's raw `partial_json` text is accumulated manually and parsed with strict `json.loads()` rather than trusting the SDK's tolerant final snapshot, and `_limpiar_fuga_json_en_resumen()` defensively truncates `resumen_general` if it ever contains an escaped, validly-parseable copy of another schema field (a real, occasionally-observed Claude generation artifact under this schema's size) — worst case a user sees a slightly shorter summary, never raw leaked JSON. Frontend counterpart: `leerRespuestaEnStream()` in `orquestador_render.js`, which reads the body stream, skips heartbeat lines, and treats the last line as the result.
- **Versioning**: uploading a new file with `version_de_id` set links it to a prior `ScoreAnalysis`. `comparar_versiones()` deterministically re-checks each mechanically-executable suggestion (`accion_tipo` of `transponer_octava`/`silenciar`) from the prior analysis against the new file's actual notes in that measure range (silence check, or average-register shift past a 6-semitone threshold) and marks it `resuelto`/`no_resuelto`; free-text suggestions (`accion_tipo: null`) always come back `sin_verificar` — they can't be checked mechanically.
- **Other endpoints**: `orquestador_historial`/`orquestador_historial_detalle` list/view past analyses. `orquestador_fragmento_edicion` (GET, `accion_tipo=transponer_octava` only) re-parses the original file and returns before/after MusicXML fragments (`generar_fragmento_comparado`, ±`CONTEXTO_COMPASES` measures of unchanged context, edited notes colored red) for OSMD side-by-side rendering in the browser. `orquestador_exportar_pdf` renders `orquestador_pdf.html` through `xhtml2pdf`/`pisa`; because the PDF can't run the frontend JS that draws the register map and density heatmap, `_calcular_mapa_registros`/`_preparar_densidad_pdf` recompute those same visuals as plain Python/CSS. `orquestador_generar_link`/`orquestador_revocar_link` issue/clear a random `share_token` (`secrets.token_urlsafe(32)`); `orquestador_publico` is the only view in this whole feature without `@login_required` — anyone with the token URL gets a read-only page.
