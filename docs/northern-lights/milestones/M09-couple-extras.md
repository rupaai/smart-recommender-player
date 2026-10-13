# M9 Couple extras

**Features:** U4–U6, W6–W11.

## Design
- **Seen together (U4):** when "Both of us" finishes a title, record it as seen together. A small badge appears on cards and details.
- **Watch-together pact (U5):** a "We watch this together" toggle per show. If one person starts the next unseen episode alone, show "You said you'd watch this together. Watch anyway?"
- **Watch-ahead recovery (U6):** "Mark unwatched for me" on an episode resets only that person's Jellyfin user data.
- **Choose for us (W6):** pick mood, genre and time available, then get one big pick with "Play" or "Another".
- **Mood and day of week (W7):** feed the current weekday and time into the recommender request; it already supports moods (`/api/moods`).
- **Veto (W8):** "Not tonight" hides a title until tomorrow; "Never" hides it for that person.
- **Rotating chooser (W9):** the app remembers who picked last; "Your turn, Labiba", or a random pick from mutual yeses.
- **Reactions (W10):** after finishing, ask "Loved it / Liked it / Fine / Not for us". This maps onto the existing verdict ratings (§10.7 in the handover), so it doesn't add a new rating system.
- **Spoiler hiding (W11):** for unwatched episodes, blur thumbnails and hide synopses, with tap to reveal.

## Build prompt
```
<generic header from README.md>
Milestone: docs/northern-lights/milestones/M09-couple-extras.md

Small independent features; one PR per two or three of them into northern-lights-player-app.
Reuse verdict ratings and moods from the existing app rather than adding parallel systems.
```
