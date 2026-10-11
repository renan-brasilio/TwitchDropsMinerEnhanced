# v1.2.0

## Channel selection

- **Watch** in the tracked-channel list now selects that streamer for mining instead of being a browser link that did nothing in the native app.
- The selected streamer appears in the home card, app status, menu bar status, and the channel table's **Watching** row.
- Offline channels have disabled Watch buttons. Unknown or ineligible channels report an error without changing the current stream.

## Campaign and target fixes

- Hide fully claimed campaigns and ended campaigns without an eligible, unclaimed reward that can still be claimed by the logged-in account.
- For ended campaigns that remain visible, show only rewards that are still claimable.
- Use the same filtered inventory for campaign cards, campaign totals, game-picker counts, and live updates.
- Recheck Twitch's claim-window deadline when producing inventory snapshots, even without a full inventory reload.
- Inventory refresh now clears the old target and countdown, rebinds the home target to the refreshed inventory, and restores the watching status instead of leaving it on **Adding campaigns to inventory**.
- Show the campaign name on the home target so identical reward names from different events are distinguishable.

## Verification

- Seven deterministic API/engine regression tests cover visibility, claim deadlines, final-claim removal, target replacement after refresh, channel selection, and rejection of unavailable channels.
- The release workflow now runs these tests before building and publishing.
- Live dashboard verification confirmed the home target matched the refreshed campaign's progress.
- Native macOS UI automation confirmed that clicking **Watch** changes the mining channel.

## Installation

Download `TwitchDropsMinerEnhanced-macOS-arm64.dmg`, quit the old app, and drag `TwitchDropsMinerEnhanced.app` into **Applications**. A portable ZIP is also available.
