# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Two people, a couple, choosing what to watch together. Used mostly on a phone on the sofa in the evening and on a laptop while planning the evening. One of them maintains the system.

## Product Purpose
A self-hosted personal cinema companion that recommends movies and TV shows for the two of them, learns both tastes from their ratings, fetches the chosen title and files it into Jellyfin so it plays on the TV. Success: picking something you both enjoy takes less time than scrolling a streaming service, and picks get better the more you rate.

## Positioning
It knows two specific people. Recommendations are blended across both profiles (what you both liked, what one loved and the other hasn't seen), it serves series in watch order, and it runs entirely on their own hardware (Raspberry Pi / Mac) with their own library.

## Operating Context
- Discover: mood, free-text description, filters → ranked picks → fetch or play.
- Rate together: one title at a time, each person rates 1–10 or says "haven't seen it"; this is the learning signal.
- Download: the only place downloads start. Title search (also opened from a Discover card), copies ranked by a Best match score with quality / size / seeders shown, progress, cancel.
- History: what was watched and what the system learned.
- Plays via Jellyfin on the TV; the web app is the remote/planner, not the player.

## Capabilities and Constraints
- Single-page PWA served by FastAPI (`templates/index.html`, `static/app.js`, `static/styles.css`); no build step, no framework. Must stay light enough for a Raspberry Pi.
- Catalogue from TMDB (posters, overviews, ratings).
- Two renameable profiles (default "Labiba" / "Gabriele"; older databases are renamed by the v11 migration).
- Product name: **Northern Lights** (chosen 2026-10-09; replaces "Movie Night" and "CouchCinema"). It is the cinema's name; the aurora appears only as holographic foil on what a ticket would stamp.

## Brand Commitments
- Light interface preferred (user stated).
- Must not feel: cute or childish; techy / like a torrent tool; a Netflix clone; a generic app template.
- No popcorn mark.
- TV surface exception (Labiba, 2026-10-28): the TV app is dark and fully aurora (polar-night ground, aurora sky, aurora-green focus), still fast and simple. The aurora-as-foil rule above applies to the phone/web pages only.

## Evidence on Hand
Real catalogue data (TMDB posters and metadata) at runtime. No testimonials, logos or brand assets exist.

## Product Principles
1. Deciding together beats browsing alone: every screen should serve the shared choice.
2. Explain the pick: say why a title is here (you both liked it, he'd recommend it, start of a series).
3. Rating should feel quick and worth it, because it is the only way the picks improve.
4. The plumbing (torrents, engines, indexers) stays out of sight.
