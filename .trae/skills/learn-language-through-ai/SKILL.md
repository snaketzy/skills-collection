---
name: learn-language-through-ai
description: Design an AI language-learning video search system from subtitles, transcripts, and HLS segments. Use when the user asks for quote-to-scene lookup, subtitle search, clip extraction, or multilingual learning playback. Do not use for generic language tutoring without media indexing.
---

# Learn Language Through AI

Use this skill to turn subtitle files, transcripts, audio tracks, and HLS playlists into a language-learning media system that can search quotes, jump to scenes, and generate study clips.

## Included References

- `references/architecture.md`
  Full technical architecture, data model, retrieval flow, HLS mapping, clip generation, and rollout guidance.
- `references/tasks.md`
  MVP implementation checklist in a `tasks.md` style, organized by milestone and dependency order.

Read only the reference that matches the current ask:

- For feasibility, system design, data modeling, storage, indexing, or API planning, read `references/architecture.md`.
- For delivery planning, execution order, MVP scope, acceptance criteria, or build sequencing, read `references/tasks.md`.
- For both architecture and implementation planning, read both references.

## Product Positioning

Treat the default roadmap as:

1. A — input a quote and return the exact time.
2. B — extract or preview the matching video clip.
3. D — scale to full-library quote search.
4. C — add actor or speaker search only after dedicated speaker-identification work.

Do not present C as something subtitles and HLS can solve by themselves.

## Core Rule

Audio alone is not searchable. A language track becomes searchable only when it has a text layer:

- existing subtitles such as `srt` or `vtt`, or
- ASR-generated transcripts with timestamps

Repeat this constraint whenever the user wants English, French, or German quote search based only on audio tracks.

## Default Response Structure

When producing a plan or implementation brief, structure the answer in this order:

1. What is feasible now across A, B, C, D
2. Required inputs
3. Data model
4. Retrieval flow
5. HLS time-to-segment mapping
6. Clip generation path
7. MVP scope
8. Phase-by-phase rollout

## New Project Guidance

When the user wants to land this in a fresh project, recommend a minimal prototype first:

- one movie
- one language track
- one subtitle or transcript source
- quote search
- exact time lookup
- HLS segment mapping
- optional clip extraction around the hit

Then expand to:

- multi-language indexing
- full catalog search
- saved study cards
- semantic retrieval
- speaker or actor attribution

Recommend this default service directory name and layout unless the user already has a house style:

- service root: `services/quote-search-api/`
- main runtime code: `services/quote-search-api/app/`
- local sample inputs: `services/quote-search-api/data/`
- tests and fixtures: `services/quote-search-api/tests/`
- temporary clip outputs: `services/quote-search-api/tmp/clips/`

If the user asks for a build-ready project layout, read the architecture and task references, then propose the full directory convention from those documents.

## Delivery Rules

- Prefer A -> B -> D -> C unless the user explicitly wants a different path.
- Separate player seek from real clip export. They are not the same feature.
- Prefer source-video clipping when a source file exists; use HLS reverse clipping only as a fallback.
- Keep speaker diarization, voiceprint matching, and actor attribution out of the MVP unless explicitly requested.
- If the user asks for a build-ready plan, use the references to provide module breakdowns, tables, API surfaces, and milestone ordering.

## Do Not Do

- Do not claim actor quote search is available from ordinary subtitles alone.
- Do not treat HLS playlists as a substitute for transcripts.
- Do not assume all language tracks already have searchable text.
- Do not collapse architecture, MVP, and future AI enhancements into one undifferentiated phase.
