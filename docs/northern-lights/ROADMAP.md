# Northern Lights app: features and milestones

Per-milestone plans and build prompts: `docs/northern-lights/milestones/`. Built from Labiba's feature spec (2026-10-10) plus the review notes in the "Jellyfin replacement" thread.

**How we'll build it:** everything goes into one long-lived feature branch, `northern-lights-player-app`. Each milestone lands there as its own small PR. We can deploy that branch to mars to try it out, but it doesn't replace the current setup until we decide to switch over. Jellyfin keeps running the whole time.

Each feature has an ID (P3, T2, and so on), so the milestones can refer to them.

---

## Part 1: Features by area

### S. Foundation (invisible, but everything depends on it)
| ID | Feature | Source |
|---|---|---|
| S1 | Backend talks to Jellyfin: login, user ids, item lookups | Jellyfin API + our code |
| S2 | Stream choice per device: tell Jellyfin what each device can play (browser, TV, Cast), so it picks direct play or conversion | Jellyfin PlaybackInfo + our code |
| S3 | Player adapter: our controls sit on top of hls.js (or Shaka), so the library can be swapped later | our code |
| S4 | Friendly "can't reach the server" screen instead of a broken player | our code |
| S5 | Right server address per place: home network for the TV, Tailscale when away | our code + config |

### P. Player
| ID | Feature | Source |
|---|---|---|
| P1 | Play/pause, title and episode name, "back to details" | player + our code |
| P2 | Skip back and ahead buttons, with a short on-screen confirmation | player |
| P3 | **Change skip length right in the player** (10/15/30/60s) | our code |
| P4 | Seek bar with elapsed, remaining and total time | player |
| P5 | Volume and mute (phone and browser) | player |
| P6 | Audio track picker with language and channels | Jellyfin API + player |
| P7 | Subtitle picker with language, forced/SDH labels and "Off" | Jellyfin API + player |
| P8 | Resume / start over choice | Jellyfin API + our code |
| P9 | Save progress regularly and when playback stops | Jellyfin API |
| P10 | Mark played/unplayed correctly | Jellyfin API |
| P11 | Next-episode card with countdown and cancel | our code |
| P12 | Autoplay on/off per show | our code |
| P13 | Keyboard shortcuts on laptop, plus a help overlay | our code |
| P14 | Phone gestures: tap to show controls, double-tap to seek | our code |
| P15 | Subtitle size, background and position | player + our code |
| P16 | Subtitle delay adjustment | player + our code |
| P17 | Playback speed | player |
| P18 | Fit / fill / original picture size | player |
| P19 | Quality picker when the stream is converted | Jellyfin API + player |
| P20 | Clear message when a format or HDR can't play | our code |
| P21 | Seek-bar thumbnail previews | Jellyfin Trickplay |
| P22 | Chapter markers and chapter list | Jellyfin API + our code |
| P23 | Skip intro / skip credits buttons, with one "always skip" setting per show | Intro Skipper plugin + our code |
| P24 | Label audio-description tracks | Jellyfin metadata |
| P25 | Controls appear on input and hide when idle | our code |

### L. Library and browsing
| ID | Feature | Source |
|---|---|---|
| L1 | Movies grid with simple cards (poster, title, year, runtime) | Jellyfin API + our code |
| L2 | Show → seasons → episodes, with watched state and next unwatched | Jellyfin API + our code |
| L3 | Details page: backdrop, synopsis, genres, Play/Resume | Jellyfin API / TMDB |
| L4 | Search movies, shows and episodes | Jellyfin API |
| L5 | Continue Watching row | Jellyfin API |
| L6 | Recently added / recently watched rows | Jellyfin API |
| L7 | "Ready to watch" vs "not in library yet" label | Jellyfin API + our downloader |
| L8 | Filters: unwatched, genre, hide from home | our code |
| L9 | Cast, crew, trailers and tech details in collapsible sections | TMDB + our code |
| L10 | Similar titles / "because you both liked…" | TMDB + recommender |

### U. People and profiles
| ID | Feature | Source |
|---|---|---|
| U1 | "Who's watching?" screen: Labiba / him / **Both of us**, no passwords on the TV | our code |
| U2 | Separate progress per person (two Jellyfin users; "Both" updates both) | Jellyfin users + our code |
| U3 | Per-person audio and subtitle language preferences | our code |
| U4 | "Seen together" marker | our code |
| U5 | Watch-together pact: warn before watching the next episode of a shared show alone | our code |
| U6 | Watch-ahead recovery: unmark an episode for one person only | our code |

### W. Watchlist and picks for two
| ID | Feature | Source |
|---|---|---|
| W1 | Shared watchlist; add from cards, details and search | our code |
| W2 | Personal lists | our code |
| W3 | Yes / maybe / pass on titles | our code |
| W4 | "For both of us" row on home | recommender |
| W5 | Short "why this pick" explanation | recommender |
| W6 | "Choose for us" with mood, genre and runtime filters | our code |
| W7 | Mood and day-of-week suggestions | recommender |
| W8 | Temporary veto vs hide forever; "not tonight" | our code |
| W9 | Rotating chooser / random pick from mutual yeses | our code |
| W10 | Quick reactions using the shared five rating verdicts | our code + recommender |
| W11 | Hide spoilers in descriptions and images | our code |
| W12 | Runtime filter ("we have 90 minutes") | our code |

### T. TV app
| ID | Feature | Source |
|---|---|---|
| T1 | TV layout: a few wide rows, big cards, clear focus | our code |
| T2 | Remote arrow-key navigation | Norigin Spatial Navigation |
| T3 | Android TV app with our name, icon and splash (installed ourselves) | Capacitor |
| T4 | Native playback and media controls on the TV | Media3 + MediaSession |
| T5 | Remote controls in the player: left/right seek, hold to speed up, back, select | Media3 + our code |
| T6 | Phone as a keyboard for TV search | our code |

### C. Casting
| ID | Feature | Source |
|---|---|---|
| C1 | Cast button on the phone and browser player | Google Cast |
| C2 | Branded receiver screen: title, backdrop, progress | custom Cast receiver |
| C3 | Phone remote while casting: pause, seek, tracks, next, stop | our code |
| C4 | Clean disconnect and hand-back | Google Cast + our code |

### D. Subtitle download
| ID | Feature | Source |
|---|---|---|
| D1 | Search subtitles from the player | Jellyfin remote subtitles + OpenSubtitles plugin |
| D2 | Results show language, SDH/forced and release match; you pick one | Jellyfin API + our code |
| D3 | Downloaded subtitle shows up and can be picked right away | Jellyfin API + our code |
| D4 | Same search from the details page | our code |

Note: the OpenSubtitles free tier has a daily download limit.

### H. Downloads, health and settings
| ID | Feature | Source |
|---|---|---|
| H1 | Download states in the new app (queued → ready / failed) | our downloader |
| H2 | Retry and a clear error | our downloader |
| H3 | Compact status: Jellyfin up, disk space, queue | Jellyfin API + our code |
| H4 | Admin screen: diagnostics, links to Jellystat / Uptime Kuma / Dozzle | our code |
| H5 | Playback defaults, with quick overrides in the player | our code |
| H6 | Short TV settings: Playback, Display | our code |

---

## Part 2: Milestones

Each milestone ends with something you can try on mars from the feature branch.

### M0: Foundation (nothing to look at yet)
S1, S2 (browser only), S3
**Done when:** opening a movie link on the laptop plays it through our own code.

### M1: Player MVP ⭐
P1–P10, P13, P25, S4, plus a bare list of movies to click
**Done when:** you can pick a movie on your laptop or phone and watch it in the Northern Lights player, change skip length in the player, switch audio and subtitles, and resume later.

### M2: Browse like a real app
L1–L5, L7, P11, P12
**Done when:** on phone or laptop you no longer need Jellyfin's web UI for movies or shows.

### M3: On the TV
T1–T5, S2 (TV), S5
**Done when:** the Northern Lights icon is on the TCL home screen, and you can browse and watch with the remote.

### M4: Who's watching
U1–U3
**Done when:** each of you has your own progress and languages, and "Both of us" works.

### M5: Subtitles that fix themselves
D1–D4, P15, P16
**Done when:** a missing or out-of-sync subtitle gets fixed without leaving the player.

**Suggested switch-over point:** after M5, Northern Lights covers everything you use Jellyfin's apps for today. That's a good time to merge the feature branch into main and make it the release. Everything after this is extra.

### M6: Picks for two
W1–W5, W12, W3
**Done when:** home shows "For both of us", and you have a shared watchlist with yes/maybe/pass.

### M7: Polish
P21, P22, P23, P14, P17, L6, L8–L10
**Done when:** thumbnails on the seek bar, skip intro, and nicer discovery.

### M8: Casting
C1–C4, T6

### M9: Couple extras
U4–U6, W6–W11

### M10: Health and settings
H1–H6, P18–P20, P24

---

## Open questions
- Does the switch-over after M5 feel right, or do you want M6 (picks for two) in the first release too?
