# ARC-AGI-3 Interface Contract (Verified)

Date captured: 2026-03-28 (UTC)
Contract source of truth:
- OpenAPI: https://raw.githubusercontent.com/arcprize/docs/main/arc3v1.yaml
- REST overview: https://raw.githubusercontent.com/arcprize/docs/main/rest_overview.mdx
- Actions reference: https://raw.githubusercontent.com/arcprize/docs/main/actions.mdx

## Contract Scope

This contract defines:
- Authentication
- Session lifecycle
- Scorecard lifecycle
- Command/action payloads
- Frame response schema
- Episode/game state transitions (as exposed by API fields)

## Base Protocol

- Base URL: https://three.arcprize.org
- Auth: HTTP header X-API-Key (required for all endpoints)
- Session affinity: preserve server cookies (especially AWSALB*) for all requests in the same game session (guid)

## Endpoint Surface

Games
- GET /api/games
  - Purpose: discover available games and game_id values
  - Response: array of Game objects

Scorecards
- POST /api/scorecard/open
  - Purpose: open a scorecard and get card_id
  - Request: optional source_url, tags, opaque
  - Response: card_id

- POST /api/scorecard/close
  - Purpose: finalize scorecard and return aggregate results
  - Request: card_id
  - Response: ScorecardSummary

- GET /api/scorecard/{card_id}
  - Purpose: fetch current/final scorecard summary
  - Response: ScorecardSummary

- GET /api/scorecard/{card_id}/{game_id}
  - Purpose: fetch scorecard summary filtered to one game
  - Response: ScorecardSummary (single-game recomputation)

Commands
- POST /api/cmd/RESET
  - Purpose: create or reset game session
  - Request: game_id, card_id, optional guid
  - Response: FrameResponse with guid and initial/reset state

- POST /api/cmd/ACTION1
- POST /api/cmd/ACTION2
- POST /api/cmd/ACTION3
- POST /api/cmd/ACTION4
- POST /api/cmd/ACTION5
- POST /api/cmd/ACTION7
  - Request: SimpleActionCommand (game_id, guid, optional reasoning <= 16KB)
  - Response: FrameResponse

- POST /api/cmd/ACTION6
  - Request: ComplexActionCommand (game_id, guid, x, y, optional reasoning)
  - x,y constraints: integer in [0, 63]
  - Response: FrameResponse

## Required Request Schemas

ResetCommand
- required: game_id, card_id
- optional: guid
- semantics:
  - no guid/null: starts new session
  - guid provided: resets existing session
  - two consecutive RESET calls guarantee fully fresh game

SimpleActionCommand
- required: game_id, guid
- optional: reasoning object (<=16KB serialized)

ComplexActionCommand
- required: game_id, guid, x, y
- x,y: integer, min 0, max 63
- optional: reasoning object (<=16KB serialized)

## Response Schemas

FrameResponse required fields
- game_id: string
- guid: string
- frame: array of frames; each frame is 64x64 integer grid
- state: one of NOT_FINISHED | NOT_STARTED | WIN | GAME_OVER
- levels_completed: integer
- win_levels: integer
- action_input: object (echo of triggering action)
- available_actions: array of action IDs currently available

Frame value domain
- each pixel value integer range is 0..15

ScorecardSummary required fields (high-level)
- card_id, score, environments, open_at, last_update
- total_environments_completed, total_environments
- total_levels_completed, total_levels, total_actions

## Episode Lifecycle (Operational)

1. Discover games via GET /api/games.
2. Open scorecard via POST /api/scorecard/open -> card_id.
3. Start run via POST /api/cmd/RESET with game_id + card_id -> guid.
4. Loop actions via ACTION1..ACTION7 using same guid and preserved cookies.
5. Observe FrameResponse state after each command.
6. Terminal states are represented by WIN or GAME_OVER.
7. Reset or start new session as needed.
8. Close scorecard via POST /api/scorecard/close when benchmark run is complete.

## Error Contract

Common status codes:
- 400: malformed request, invalid ids/fields, range violations
- 401: missing/invalid X-API-Key
- 404: unknown/missing scorecard ids on scorecard endpoints

## Non-Contractual Notes

- available_actions is dynamic per frame and should drive action masking.
- ACTION6 availability in docs indicates coordinate action may be enabled without exposing active-coordinate mask.
- The API contract defines interaction semantics. Competition runtime hardware limits are managed by Kaggle competition environment rules, not by this REST API spec.
