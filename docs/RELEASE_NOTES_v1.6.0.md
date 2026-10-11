# v1.6.0

## New: choose campaigns directly

Each card in **Campaigns & Rewards** has two new actions:

- **Mine now:** the miner switches to a channel for this campaign and mines it before anything else. This works even if its game isn't in your mining order, and even if its account link is unknown, because starting it is your explicit choice. The card is marked **Mining first** and moves to the top of the list.
  - **Stop** returns to your normal mining order.
  - The choice clears itself once the campaign is finished or over, and it survives restarts.
  - Only campaigns that have started and still have something left to earn can be started.
- **Ignore:** hides the campaign, and the miner never works on it.
  - Rewards you already earned are still claimed.
  - Ignored campaigns move to a collapsible **Ignored campaigns** list at the bottom, with **Restore**.
  - They don't count in the campaign total or in **Games → With drops**.
  - Ignoring the campaign you're mining stops it right away.

## New: refresh the drops list

**Games → With drops** has a **Refresh** button that checks Twitch again for live drop campaigns and updates the list.

## Fixes

- Drop names and images on campaign cards are now escaped, so a reward name containing HTML can't break the card.

## Tests

- 3 selection tests on the real mining state machine:
  - **Mine now** comes first, and outside the priority list it can be an unlinked game.
  - An ignored campaign is never mined, even when it is prioritized and linked.
  - A finished **Mine now** choice clears itself.
- 3 API contract tests for `/api/campaigns/ignore` and `/api/campaigns/focus`: counts, persistence, validation, and cancelling a focus by ignoring the campaign.
- 3 browser specs: Ignore and Restore, Mine now and Stop, and the drops Refresh button.
- In a live check, **Mine now** on VALORANT (not in the priority list) switched the miner to a VALORANT channel for *VCT26 Champs Shanghai*. Ignoring it stopped mining and removed it from the counts.

## Downloads

- `TwitchDropsMinerEnhanced-v1.6.0-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.6.0-macOS-arm64.zip`: macOS application archive.
