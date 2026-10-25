# v1.8.11

## Mining queue recovery

- Offline campaigns stay queued without blocking other campaigns with eligible live streams. A focused campaign resumes first when its allowed broadcaster becomes available.
- Rediscover channels for queued games every five minutes, alongside PubSub events and hourly inventory reloads. Retained offline allowlisted channels are rechecked even if a live event was missed.
- Allow selected campaigns to accrue watch time before publisher-account linking. Keep the link warning and publisher connection link visible: linking is still needed to receive rewards in-game. Ignored/excluded games and the selected-games-only mode remain respected.
- Show per-campaign queue states: mining, live and queued, waiting for named broadcasters, paused, upcoming, or outside the mining queue.
- Preserve manual Pause across background discovery and live events. Discard progress from watch requests that finish after a pause or channel switch instead of restoring a stale active target.

Regression scenarios cover offline-focus fallback, rechecking retained channels, newly live directory streams, focused-campaign completion, queue-state transitions, and pause races. The isolated application/browser smoke exercises waiting, live fallback, pause/resume, and automatic queue advancement.
