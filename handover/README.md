# Handover notes

Two PRs that edit the same lines of PROJECT_HANDOVER.md would conflict. So PRs never edit the feature
blocks there. Each PR adds a note instead. A script (no AI) copies the notes into PROJECT_HANDOVER.md
later.

## What to do in a PR

1. Make a file `handover/notes/<feature>/<name>.md`. Use the branch name as the file name.
   Copy [TEMPLATE.md](TEMPLATE.md). The feature name is the one in the
   `feature:` marker (a hidden comment) around its text in PROJECT_HANDOVER.md.
2. Write `## Line`: the full new text of the feature's block. Write `## Open work` if the
   open work changed (`none` removes it). `## What changed` is for readers while the note waits.
3. Keep `status: in-progress` while the work goes on. Set `status: ready` when the feature is done.
4. A brand new feature has no block yet. Add `new: true` to the note.
5. One PR can add notes for several features.

If the PR really needs no note, label it `no-note`. PRs titled docs, chore, test, ci, style,
refactor or build need no note.

## What happens next

- CI runs `python3 scripts/handover.py check` on every PR. It fails if a note is broken, if the
  PR edits a feature block, or if a feature PR has no note.
- After a merge to `main`, `python3 scripts/handover.py fold` runs. For each feature whose newest
  note is `ready`, it puts the note's Line (and Open work) into PROJECT_HANDOVER.md and deletes that
  feature's notes. A bot opens the `docs: fold handover notes` PR with the result.
- Only a `plan:` pointer is kept. Details stay in the PR.

You can run both commands locally. Everything outside the markers in PROJECT_HANDOVER.md (rules, ports,
how to build) can still be edited directly.
