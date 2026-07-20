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

Environment variables (all optional, have dev-safe defaults except the Gemini key):
- `DJANGO_SECRET_KEY` — falls back to a hardcoded insecure key.
- `DJANGO_DEBUG` — `'True'`/`'False'` string, defaults to `False`.
- `GEMINI_API_KEY` — required for the AI orchestration-analysis feature (`orquestador_analizar`) to actually call Gemini; without it, or without `google-genai` installed, the view degrades to returning the raw extracted music data instead of an AI critique.

One-off data seed scripts at the repo root (`seed_pieces.py`, `seed_gamification.py`, `seed_dictado.py`, `seed_solfeo.py`, `seed_recommended.py`, `fix_req.py`) are run directly with `python <script>.py`, not via `manage.py` — each calls `django.setup()` itself. They populate `Game`, `Achievement`, `Piece`, `DailyGoal` fixture data. Read one before running to know what it seeds/overwrites.

## Architecture

### Data model shape (`trainer/models.py`)

Everything lives in one app, roughly four families of models:

1. **Ear-training games** — `Game` (config: slug, recommended accuracy/attempts to mark "completed"), `Score` (per-user-per-game XP/streak/accuracy, `unique_together('user','game')`), `Attempt` (individual answer log used for per-question-type stats).
2. **Gamification** — `UserProfile` (one per user: total XP, level, daily streak, personal records), `Achievement`/`UserAchievement` (unlocked via slug lookups in `check_achievements()` in `views.py`).
3. **Sheet music / practice tracking** — `Piece` (MusicXML text blob for the games), `SheetMusic`/`Collection`/`Favorite` (the "biblioteca"), `StudySession`, `SheetMusicProgress`, `Playlist`/`PlaylistSheet`, `RehearsalConfig`/`RehearsalLog` (BPM ramp practice), `SheetMarker`/`SheetNote`, `MusicalProject`/`ProjectGoal`/`ProjectSection` (longer-term repertoire tracking with per-section mastery status).
4. **MIDI trainer** — `MidiChordStat` (per-user per-chord mastery, auto-flagged `is_mastered`/`is_problematic` from rolling accuracy), `MidiGameSession`.
5. **Orchestration AI feature** — `ScoreAnalysis` stores an uploaded `.mid`/`.musicxml` file plus a `JSONField` with the structured AI critique.

There are no `ModelForm`s or DRF serializers — views read `request.POST`/`json.loads(request.body)` directly and validate ad hoc.

### Views (`trainer/views.py`, ~960 lines, one flat module)

- Every game view follows the same pattern: `get_object_or_404(Game, slug=...)`, `Score.objects.get_or_create(...)`, optionally `get_game_stats(user, game)` for the "hardest questions" breakdown, then render a dedicated template.
- `record_attempt` (POST, one endpoint shared by all games via `game_slug`) is the central scoring path: logs an `Attempt`, updates `Score` (streak/points/level via `Score.level_info`), updates `UserProfile` (daily streak, XP, `get_user_level_for_xp`), calls `check_achievements`, and returns the new stats as JSON for the frontend to render without a page reload.
- XP/leveling logic is duplicated in two places that must stay in sync conceptually but are independent: `get_user_level_for_xp()` (global user level, flat XP thresholds in `views.py`) and `Score.level_info` (per-game level, different threshold table in `models.py`).
- `orquestador_analizar` is the odd one out: parses an uploaded score with `music21` (key/time signature/tempo/measures/dynamics per part), then — if `google-genai` is installed and `GEMINI_API_KEY` is set — sends that structured data to `gemini-2.0-flash` with a Pydantic `response_schema` (`OrchestrationAnalysis`) forcing structured JSON output critiquing orchestration balance/doubling. Falls back to returning the raw extracted data when the SDK/key is unavailable.
- Most mutating endpoints are `@csrf_exempt` + `@login_required` and accept JSON bodies (not Django forms) — POST-only, manual `try/except` returning `{'status': 'error', 'message': str(e)}`.
- Almost every view does local `from .models import X` inside the function body rather than at module scope (existing convention — follow it for new views touching models already imported elsewhere, to avoid circular churn at the top).

### URLs (`trainer/urls.py`)

Flat, no routers/viewsets. Game URLs follow `juego/<slug>/`; most API endpoints are `api/<action>/` or `api/<action>/<id>/`. `trainer.urls` is included at the site root in `config/urls.py`, alongside `django.contrib.auth.urls` for login/logout/password views (see `templates/registration/`).

### Frontend

No JS bundler/build step. Per-page logic lives inline in `<script>` blocks inside each `templates/trainer/*.html` file (some are 900+ lines, mixing markup + game logic). Shared pieces:
- `templates/base.html` — nav, global CSS variables, the "Aprendizaje/Evaluación" mode toggle (persisted in `localStorage['app_mode']`, read via `window.getAppMode()`), and the shared theory-explanation modal (`window.showTheoryModal(title, html, callback)`).
- `static/js/teoria.js`, `audio_engine.js`, `pitch_tracker_blueprint.js` — the only shared JS modules (music theory helpers, Tone.js-based audio playback, pitch detection).
- Libraries are loaded via CDN `<script>` tags in `base.html` (Tailwind CDN, Tone.js, Tonal.js); VexFlow is loaded per-template where needed for notation rendering.
- Game pages call the shared `/api/record_attempt/<game_slug>/` endpoint via `fetch` and update their own DOM/state from the JSON response — there's no shared frontend state management.

### Static/media

`STATICFILES_DIRS`/`STATIC_ROOT` and `MEDIA_ROOT` are configured normally; `static/audio/piano/` holds piano note samples served to the browser for the audio engine. `MEDIA_URL`/media serving is only wired up when `DEBUG=True` (see `config/urls.py`) — uploaded `SheetMusic.xml_file` and `ScoreAnalysis.score_file` need a real media-serving setup in production.
