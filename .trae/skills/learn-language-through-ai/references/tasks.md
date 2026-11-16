# Learn Language Through AI MVP Tasks

## Goal

Ship a buildable MVP that can search a quote in one movie, return the exact time, map the result to HLS segments, and optionally export a short clip.

## Principles

- Build A before B
- Build B before D
- Keep C out of MVP
- Treat subtitles or ASR transcripts as required searchable text
- Prefer source-video clipping over HLS reverse clipping

## Milestone 0 - Project Skeleton

Recommended service root for the MVP:

- `services/quote-search-api/`

Recommended first-pass tree:

```text
services/quote-search-api/
├── app/
├── data/
│   ├── subtitles/
│   ├── playlists/
│   └── transcripts/
├── scripts/
├── tests/
│   └── fixtures/
└── tmp/
    └── clips/
```

- [ ] Create service directory for the prototype implementation
- [ ] Choose backend runtime such as FastAPI or Node.js
- [ ] Add dependency manifest
- [ ] Add local configuration example
- [ ] Add sample data directory layout for subtitles, playlists, and outputs
- [ ] Add README-equivalent launch instructions only inside the prototype service, not the skill

Acceptance criteria:

- The service boots locally
- One test asset can be loaded from local paths

## Milestone 1 - Subtitle and Transcript Ingestion

- [ ] Implement `srt` parser
- [ ] Implement `vtt` parser
- [ ] Normalize cue timestamps to floating-point seconds
- [ ] Normalize cue text for search
- [ ] Persist `media_asset`, `media_track`, and `subtitle_cue`
- [ ] Add fixture files for one movie and one language

Acceptance criteria:

- A subtitle file can be imported into storage
- Cues retain correct order and timing
- Raw and normalized text are both stored

## Milestone 2 - HLS Timeline Ingestion

- [ ] Parse master playlist
- [ ] Parse media playlist
- [ ] Read `EXTINF` durations
- [ ] Build cumulative segment time ranges
- [ ] Persist `hls_variant` and `hls_segment`
- [ ] Support `.ts` and `.m4s` segment URIs

Acceptance criteria:

- Any cue time can be mapped to a segment index
- Segment start and end times are queryable

## Milestone 3 - Quote Search API

- [ ] Add `POST /search/quotes`
- [ ] Support exact phrase search
- [ ] Support keyword search
- [ ] Support language filtering
- [ ] Return cue context around the match
- [ ] Map each hit to its HLS segment and in-segment offset

Acceptance criteria:

- A known quote returns the expected cue time
- Response includes segment index and offset

## Milestone 4 - Clip Preview and Export

- [ ] Add clip window calculation around a hit
- [ ] Add preview response with `seek_time`
- [ ] Add `POST /clips/create`
- [ ] Prefer source-video clip export when source file exists
- [ ] Save exported clip metadata
- [ ] Return job status and output path or URL

Acceptance criteria:

- The service can export a short clip around a matched quote
- Exported clip timing is within acceptable tolerance of the cue

## Milestone 5 - MVP UI or Consumer Contract

- [ ] Define frontend response contract for quote search results
- [ ] Define player behavior for seek and subtitle highlighting
- [ ] Expose clip creation status polling or callback flow
- [ ] Add simple local demo page or API collection

Acceptance criteria:

- A user can search a quote and jump to the scene
- A user can request clip export from the same result

## Milestone 6 - Reliability and Validation

- [ ] Add tests for subtitle parsing
- [ ] Add tests for HLS time mapping
- [ ] Add tests for quote search normalization
- [ ] Add tests for clip window calculation
- [ ] Add a fixture-based end-to-end test for one known quote

Acceptance criteria:

- Tests cover the golden path from quote input to segment mapping
- At least one fixture proves stable timing behavior

## Post-MVP Expansion

### Phase 2

- [ ] Add multiple language tracks per asset
- [ ] Add ASR transcript ingestion for audio-only languages
- [ ] Add multi-movie indexing
- [ ] Add saved study cards

### Phase 3

- [ ] Add OpenSearch or Elasticsearch when PostgreSQL search becomes limiting
- [ ] Add semantic search for similar quotes
- [ ] Add clip caching and background job queue

### Phase 4

- [ ] Add speaker diarization
- [ ] Add speaker identification or actor attribution
- [ ] Add actor or role scoped quote search

## Suggested File and Module Breakdown

- [ ] `app/models/` for asset, track, cue, and segment models
- [ ] `app/parsers/` for `srt`, `vtt`, and `m3u8`
- [ ] `app/indexing/` for normalization and search logic
- [ ] `app/api/` for quote and clip endpoints
- [ ] `app/repositories/` for persistence code
- [ ] `app/services/` for ingestion, retrieval, and clipping
- [ ] `data/subtitles/` for local subtitle fixtures
- [ ] `data/playlists/` for local HLS fixtures
- [ ] `data/transcripts/` for ASR transcript fixtures
- [ ] `tmp/clips/` for generated clip output
- [ ] `tests/fixtures/` for one movie's sample inputs

## Risks to Track

- [ ] Subtitle timing drift versus source media
- [ ] HLS playlist and source-video timeline mismatch
- [ ] Language normalization differences across English, French, and German
- [ ] Slow clip export on large source files
- [ ] Missing searchable text for some language tracks

## Definition of MVP Done

- [ ] One movie is fully ingested
- [ ] One language track is searchable
- [ ] One quote can be found by text
- [ ] The quote returns exact cue timing
- [ ] The cue maps to HLS segment and offset
- [ ] The system can seek to the scene
- [ ] The system can export a short study clip
