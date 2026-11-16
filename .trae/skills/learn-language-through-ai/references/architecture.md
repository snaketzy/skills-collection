# Learn Language Through AI Architecture

## Goal

Build a media indexing system that lets users search spoken lines in movies or shows, jump to the matching scene, and optionally extract short learning clips. The system is designed for English, French, German, and any other language that has a searchable text layer.

## Feasibility Summary

### A. Quote to exact time

Fully feasible with subtitles or timed transcripts.

Input:

- subtitle files such as `srt` or `vtt`, or
- ASR transcript with timestamps

Output:

- matched quote
- `start_sec`
- `end_sec`
- nearby context lines

### B. Quote to extracted clip

Fully feasible once quote timing is known.

Flow:

- find quote timing
- expand a clip window around the hit
- map the window to HLS time
- either seek in player or export a clip

### C. Actor to all lines and clips

Not a subtitle-only feature. Requires a separate attribution pipeline:

- speaker diarization
- speaker identification or voiceprints
- optionally face recognition or shot alignment

Treat this as a later-phase subsystem.

### D. Full-library quote search

Fully feasible and should be the long-term product direction. It scales A and B across the catalog.

## Key System Principle

The platform works by combining three layers:

1. Text layer
   Searchable subtitle or transcript text
2. Timeline layer
   Unified cue and media timestamps
3. Media layer
   HLS master and media playlists with segment time ranges

Core chain:

`query -> quote match -> cue time -> segment lookup -> seek or clip`

## Required Inputs

At minimum, each searchable asset needs:

- a stable `asset_id`
- one playable video source or HLS URL
- one searchable text source for the target language

Preferred optional inputs:

- source video for accurate clip export
- multiple audio tracks
- multiple subtitle or transcript tracks
- movie, episode, actor, and language metadata

## High-Level Architecture

Use four logical layers:

### 1. Ingestion

Responsibilities:

- accept source metadata
- parse `srt` and `vtt`
- parse HLS master and media playlists
- detect audio and subtitle tracks
- run ASR when no subtitle exists for a language

Outputs:

- normalized text cues
- normalized segment timeline
- asset and track metadata

### 2. Indexing

Responsibilities:

- store cue-level searchable text
- store time-aligned HLS segments
- store track-level metadata
- support exact and keyword search

### 3. Retrieval

Responsibilities:

- quote search
- keyword and phrase match
- language filtering
- result ranking
- context aggregation

### 4. Playback and Clip

Responsibilities:

- player seek to hit time
- subtitle highlighting
- clip extraction
- study-card creation and save actions

## Recommended Directory Convention

When landing this system in a new project, use one clear service root instead of scattering ingestion, search, and media logic across the repo.

Recommended root:

- `services/quote-search-api/`

Recommended layout:

```text
services/quote-search-api/
├── app/
│   ├── api/
│   ├── core/
│   ├── indexing/
│   ├── models/
│   ├── parsers/
│   ├── repositories/
│   └── services/
├── data/
│   ├── assets/
│   ├── subtitles/
│   ├── playlists/
│   └── transcripts/
├── scripts/
├── tests/
│   ├── fixtures/
│   └── integration/
├── tmp/
│   ├── clips/
│   └── ingest/
└── pyproject.toml
```

Directory roles:

- `app/api/`
  HTTP endpoints such as quote search and clip creation
- `app/core/`
  configuration, settings, shared utilities, and app bootstrap
- `app/indexing/`
  normalization, tokenization, ranking, and search orchestration
- `app/models/`
  schema definitions for asset, track, cue, segment, and clip job
- `app/parsers/`
  `srt`, `vtt`, and `m3u8` parsers
- `app/repositories/`
  database access and persistence boundaries
- `app/services/`
  ingestion, retrieval, time mapping, and clip generation workflows
- `data/assets/`
  local source videos or asset metadata fixtures for development only
- `data/subtitles/`
  sidecar `srt` and `vtt` test inputs
- `data/playlists/`
  master and media playlist fixtures
- `data/transcripts/`
  ASR output fixtures
- `scripts/`
  one-off importers and local maintenance commands
- `tests/fixtures/`
  stable sample inputs for parser and search tests
- `tests/integration/`
  end-to-end quote-to-segment test cases
- `tmp/clips/`
  generated preview or exported clips that should not be committed
- `tmp/ingest/`
  ephemeral ingest artifacts and normalized intermediates

If the target project already has a backend monorepo convention, keep the same top-level layout and map these folders into the existing service pattern instead of forcing a second structure.

## Data Model

### media_asset

- `asset_id`
- `movie_id`
- `title`
- `duration_sec`
- `source_video_path` nullable
- `master_playlist_url`
- `default_audio_lang`
- `created_at`

### media_track

- `track_id`
- `asset_id`
- `track_type` with values such as `audio`, `subtitle`, `transcript`
- `language`
- `label`
- `source_type` with values such as `embedded`, `sidecar`, `asr`
- `is_default`

### subtitle_cue

- `cue_id`
- `asset_id`
- `track_id`
- `cue_index`
- `start_sec`
- `end_sec`
- `text_raw`
- `text_normalized`
- `speaker_label` nullable

### hls_variant

- `variant_id`
- `asset_id`
- `bandwidth`
- `resolution`
- `playlist_url`

### hls_segment

- `segment_id`
- `asset_id`
- `variant_id`
- `segment_index`
- `uri`
- `start_sec`
- `end_sec`
- `duration_sec`
- `byte_range` nullable
- `part_index` nullable

### clip_job

- `clip_id`
- `asset_id`
- `start_sec`
- `end_sec`
- `language`
- `status`
- `output_url` nullable

## Indexing Strategy

## Text Index

Normalize each quote or cue before indexing:

- lowercase
- Unicode normalization
- punctuation cleanup
- whitespace cleanup
- language-aware tokenization
- optional lemma or stemming

Search modes to support first:

- exact phrase match
- keyword match
- prefix match
- language filter

Recommended storage:

- MVP: PostgreSQL with full-text or trigram support
- Scale: OpenSearch or Elasticsearch

## Time Index

Convert all timeline values to floating-point seconds once during ingestion.

Useful indexes:

- `subtitle_cue(asset_id, track_id, start_sec)`
- `subtitle_cue(track_id, text_normalized)`
- `hls_segment(asset_id, variant_id, start_sec, end_sec)`

## HLS Time Mapping

Parse:

- master playlist
- media playlists
- `EXT-X-MEDIA`
- `EXTINF`
- `EXT-X-MAP`
- `EXT-X-BYTERANGE`

Build segment time ranges by cumulatively summing `EXTINF` durations in playlist order.

For fMP4 and CMAF:

- `.m4s` files are still treated as sequential time buckets
- use playlist order and declared durations

Lookup rule for a quote time `t`:

- find the segment where `start_sec <= t < end_sec`

Return:

- `segment_index`
- segment `uri`
- `offset_in_segment_sec = t - start_sec`

For clip windows, gather the full segment range overlapping:

- `clip_start`
- `clip_end`

## Retrieval Flow

### Subtitle-first flow

1. User submits a phrase or keyword
2. Detect or receive target language
3. Search the matching subtitle or transcript track
4. Return matching cue rows
5. Map cue time to HLS segments
6. Return seek time plus clipable window

### Audio-only flow

1. Run ASR for each target language track
2. Generate timed transcript cues
3. Reuse the same retrieval path as subtitles

This is the critical step if the product promise is "search English, French, or German as long as the movie has that audio track."

## Clip Strategy

### Option 1. Player seek only

Use when the goal is fast playback:

- return `asset_id`
- return `seek_time`
- highlight current subtitle cue

### Option 2. Export actual clip

Use when the user needs a saved learning snippet.

Preferred order:

1. Clip from source video if available
2. Reverse-build from HLS only when source video is unavailable

Suggested default window:

- `clip_start = max(0, cue_start - 8)`
- `clip_end = cue_end + 8`

Example command:

```bash
ffmpeg -ss <clip_start> -to <clip_end> -i <source> -c:v libx264 -c:a aac output.mp4
```

## Multi-Language Strategy

Treat each language track as its own searchable text layer.

Recommended per asset:

- English subtitle or transcript
- French subtitle or transcript
- German subtitle or transcript

Supported search behaviors:

- language-scoped search
- automatic query language detection
- same-scene alignment across languages

This allows study flows such as:

- search in English
- play English audio
- display French or German subtitles
- save parallel quote cards

## MVP Scope

Keep the first working version narrow:

- one movie
- one language
- one subtitle or transcript track
- quote search
- exact time return
- HLS segment mapping
- optional 8-second pre and post clip export

Avoid in MVP:

- actor attribution
- cross-language semantic search
- embeddings
- live ASR at query time
- HLS-only clipping optimization

## Suggested API Surface

### `POST /search/quotes`

Request:

```json
{
  "query": "we need to keep moving",
  "language": "en",
  "movie_id": "optional",
  "limit": 10
}
```

Response:

```json
{
  "hits": [
    {
      "asset_id": "movie_001",
      "track_id": "sub_en",
      "cue_id": "cue_1892",
      "text": "We need to keep moving.",
      "start_sec": 4123.52,
      "end_sec": 4126.14,
      "segment_index": 1030,
      "offset_in_segment_sec": 2.37
    }
  ]
}
```

### `POST /clips/create`

Request:

```json
{
  "asset_id": "movie_001",
  "start_sec": 4115.52,
  "end_sec": 4134.14,
  "language": "en"
}
```

Response:

```json
{
  "clip_id": "clip_7781",
  "status": "queued",
  "preview_seek_time": 4123.52
}
```

## Recommended Tech Stack

MVP:

- FastAPI or Node.js backend
- PostgreSQL
- ffmpeg
- mature m3u8 parsing library

Scale-up:

- OpenSearch or Elasticsearch
- Celery, Dramatiq, or BullMQ
- S3 or R2 clip storage
- embeddings for semantic similarity

## Rollout Sequence

1. Single-asset subtitle search
2. HLS time mapping
3. Clip generation
4. Multi-language expansion
5. Full-library indexing
6. Saved study cards
7. Speaker and actor attribution

## Success Metrics

- quote search hit rate
- phrase match precision
- time-location accuracy
- clip generation success rate
- clip wait time
- save or replay rate
