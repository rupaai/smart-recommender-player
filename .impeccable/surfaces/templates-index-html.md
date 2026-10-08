---
version: 1
slug: "templates-index-html"
primary_target: "templates/index.html"
related_targets: ["static/styles.css","static/app.js"]
---

Scope: the whole single-page app (Discover, Download, Rate together, History). Mode: Operate.
Audience/job: a couple choosing tonight's film, phone on the sofa or laptop. Rate quickly so picks improve.
Constraints: light UI; not cute, not techy/torrent, not Netflix, not template. Standard controls. No build step, Pi-friendly. Name is a placeholder ("Movie Night").
Trial: two looks ship side by side behind a header switch (data-look on <html>); the user picks one after, the other is deleted.

## Direction contract
THESIS: The evening's choice is issued, not browsed: picks are admission tickets for two. Refuses the dark poster-wall streaming grid.
OWN-WORLD (ticket): neutral light paper ground, white ticket cards, roll-ticket stocks as roles (tangerine = act, butter = selected, rose = together, mint = ready/play, sky = learned taste), ink text; condensed heavy numerals/headings (Archivo condensed → Avenir Next Condensed), workhorse sans body. OWN-WORLD (clean, user-requested alternate): Apple-style light greys, white cards, one blue accent, system type.
STORY: say what you're in the mood for, get a short ranked list that says why each title is here, fetch or play, rate it after.
FIRST VIEWPORT: header (name, look switch, TV status); top tab bar on desktop / bottom bar on phone; Movies|TV and Us two|Just me|His picks segmented; mood heading; describe box; mood ticket grid in stock colours; collapsed More filters; sticky tangerine "Find a movie for tonight".
FORM: ticket stub, candidate 7 of 7 (seed 7bdd5fee, degraded roll). Signature move: each pick is a ticket with a perforated, notched stub holding its number ("No. 01") and the action; the rating card's stub holds both people's verdicts.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
