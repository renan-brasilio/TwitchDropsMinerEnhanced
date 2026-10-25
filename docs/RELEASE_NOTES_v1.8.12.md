# v1.8.12

## Reliable mining queue and macOS installation

Includes the queue recovery changes from v1.8.11:
- Keep offline campaigns waiting while campaigns with eligible live streams continue mining.
- Recheck queued games and offline allowlisted channels every five minutes, alongside live events.
- Resume selected campaigns even before publisher linking, with a separate link-to-receive-rewards warning.
- Display mining, queued, waiting, and paused campaign states. Respect manual Pause and discard stale in-flight progress.

The macOS build now ad-hoc signs the complete application after assembling its resources, then verifies the bundle before packaging. This fixes launch rejection of CI-built applications whose executable signature did not seal the assembled bundle. This is local ad-hoc signing, not Apple notarization.
