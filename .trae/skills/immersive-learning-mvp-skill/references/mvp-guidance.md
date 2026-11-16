# Immersive Learning MVP Guidance

## Core Judgment

The product direction is viable, but only if the first version is much narrower than the vision.

The idea becomes risky when it tries to include too many of these at once:

- multiple worlds
- multi-subject coverage
- 3D environments
- video generation
- real-time voice interaction
- fully open AI NPC behavior
- detailed knowledge maps

These belong to a longer roadmap, not to an MVP.

## Why The Direction Is Promising

The product has several strong qualities:

1. It leads with curiosity instead of homework.
2. AI is naturally useful for NPCs, hints, dialogue, and adaptive difficulty.
3. A single scenario can embed language, logic, math, and communication.
4. If the experience is good, users may describe it as a world to explore rather than a lesson to endure.

## Main Product Risks

### Scope Explosion

Big immersive learning visions often combine too many product bets into one launch. That usually delays validation and raises cost before anyone proves retention.

### Chatbot Trap

Without a strong task loop, the experience collapses into a polished chatbot. Novelty may be high, but repeat use drops fast.

### Knowledge Accuracy

Math, language explanation, grammar correction, and history facts cannot rely on unconstrained model generation. Use structure, rules, and bounded answer spaces where possible.

### Cost And Latency

LLM chat, speech recognition, TTS, image generation, and dynamic content all add latency and operating cost. Early versions should make high-cost features optional.

### Unclear Audience

"People who dislike learning" is not a sufficient segment. The MVP should specify:

- age range
- learning goal
- use case
- buyer or payer

## Recommended MVP Shape

Default recommendation:

- one web-based world
- one target user group
- one repeatable interaction loop
- 3-5 high-quality sessions
- lightweight capability tracking

### Best First World

A detective world is the strongest default starting point because it naturally supports:

- reading clues
- asking questions
- timeline and number reasoning
- light English fragments
- structured conclusions

It delivers immersion without requiring 3D.

## Suggested Core Loop

Use a loop like this:

1. Receive the case
2. Review clues
3. Question NPCs
4. Organize evidence
5. Make a deduction
6. Receive resolution and feedback

This works better than unrestricted chat because the user always knows what progress looks like.

## Minimum MVP Capabilities

### User-Facing

- interest-based landing page
- case or mission selection
- clue reading and evidence review
- constrained AI conversations with characters
- hint system with multiple levels
- conclusion or submission step
- end-of-case feedback

### System

- user progress storage
- case state management
- dialogue logging
- structured case content
- simple analytics

## What To Postpone

Postpone these unless the user explicitly asks for a later-stage roadmap:

- 3D worlds
- generated video scenes
- multi-world launch
- full curriculum coverage
- open-ended sandbox interaction
- social systems
- full knowledge graph engine

## Content Design Rules

Each case should be structured with:

- case summary
- character profiles
- visible clues
- hidden clues
- correct reasoning chain
- common wrong paths
- layered hints
- resolution feedback

Without this structure, AI output becomes inconsistent and quality drifts.

## Team Guidance

The minimum practical team is usually:

1. product or founder
2. full-stack builder
3. content designer

One person can cover more than one role, but all three functions must exist.

## Timeline Benchmarks

### Experienced Small Team

- 2-3 weeks — clickable or demo-grade prototype
- 6-8 weeks — internal playable MVP
- 10-12 weeks — small-scale user-testable version

### Experienced Solo Builder

- 4-6 weeks — prototype
- 10-14 weeks — basic MVP
- 14-18 weeks — more stable test version

### Beginner Learning While Building

- 6-10 weeks — rough demo
- 4-6 months — realistic MVP
- 6-9 months — more stable user-testable build

## Default Final Recommendation

When the user asks for a bottom-line recommendation, say:

- the idea is viable
- the MVP is viable
- the full original vision is not a first-release scope

Then recommend starting with an AI detective learning product on the web rather than a full immersive learning universe.
