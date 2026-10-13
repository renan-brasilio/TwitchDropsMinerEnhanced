# v1.8.4

## Improvements: Active Target card, header actions, and Dokploy integration

- **Cleaner Active Target empty state**: When there is no active campaign, the Cockpit card hides the placeholder thumbnail, idle metadata tags, progress percentages, and empty progress bar. It displays a clean prompt: "To start, select one from Campaigns & Rewards and click Resume, or enable “Automatically select linked games”."
- **Context-aware Pause & Resume/Start buttons**:
  - **Pause** is only visible while actively mining a campaign.
  - **Start / Resume** appears only when mining is paused or idle and there is at least one available campaign to start (labeled "Resume" when paused, "Start" when idle). Both are hidden if no campaigns are available.
- **Dynamic Claim Drops button**:
  - When **Auto-claim drops** is enabled, the button displays "Automatically claiming" (disabled).
  - When **Auto-claim drops** is off, the button appears only if there are pending drops ready to be claimed.
- **Dokploy integration**: Registered the service and domain directly in Dokploy's database on the VPS under the **Automations** project.
