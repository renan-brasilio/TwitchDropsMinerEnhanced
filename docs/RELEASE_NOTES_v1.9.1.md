# v1.9.1

## Compact campaign queue status for stream waiting

- Replace full channel allowlist dumps in the `Waiting for stream` status pill with clean, concise labels:
  - When multiple streamers are eligible: `Waiting for an eligible streamer to be live`
  - When a single streamer is eligible: `Waiting for <streamer> to be live`
  - When open to any channel: `Waiting for an eligible live stream`
- Move channel details to the hover tooltip (`title`), summarizing up to 5 allowed streamers plus remaining count without overflowing the screen.
