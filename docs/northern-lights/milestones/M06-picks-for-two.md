# M6 Picks for two

**Features:** W1–W5, W3, W12.
**Done when:** home shows a "For both of us" row with a short reason under each pick, and there's a shared watchlist with yes/maybe/pass.

## Design
- **Reuse what exists:** the current app already has a shared Watch Later list (schema v11, `/api/watch-later`) and the recommender (`/api/recommend`, co-view ratings). M6 puts them into the new UI. It doesn't build a new recommender.
- **Watchlist (W1, W2):** "+ Watchlist" on cards, details and search. Shared by default; "Just me" makes it personal (add an owner column to `watch_later`, schema bump).
- **Yes / maybe / pass (W3):** per person per title, in a new `nl_interest` table. "Both said yes" items go to the top of the watchlist and feed the recommender as positive co-view signals.
- **For both of us (W4, W5):** a home row that calls the recommender in co-view mode for the viewers on screen. Each card shows a one-line reason ("You both loved Arrival").
- **Runtime filter (W12):** chips like "Under 90 min" and "Under 2 h" on the For-both row and the watchlist.

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M06-picks-for-two.md

Wire the existing watch-later list and recommender into the /nl/ UI (web and TV layouts), add
interest states and the runtime filter. Read §10 and §12 of PROJECT_HANDOVER.md first; reuse
existing endpoints where possible. Tests + make check, handover + README, PR into
northern-lights-player-app.
```
