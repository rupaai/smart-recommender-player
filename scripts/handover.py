#!/usr/bin/env python3
"""Conflict-free handover notes. Python 3 standard library only.

Each feature's text in the handover doc sits between markers:
    <!-- feature:NAME -->  ...  <!-- /feature:NAME -->     (its state)
    <!-- open:NAME -->     ...  <!-- /open:NAME -->        (its open work)
A PR never edits these. It adds a note, handover/notes/NAME/<file>.md. When the newest
note of a feature says `status: ready`, `fold` copies the note into the doc and deletes
the feature's notes. `check` is the CI gate. See handover/README.md.

    python3 scripts/handover.py check --base origin/main --title "feat: x" [--skip]
    python3 scripts/handover.py fold
"""
import argparse
import os
import re
import subprocess
import sys

DOCS = ("HANDOVER.md", "PROJECT_HANDOVER.md")  # the first one that exists is used
NOTES = "handover/notes"
NEW_FEATURES = "<!-- new-features -->"
NEW_OPEN = "<!-- new-open-work -->"
SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
BLOCK = re.compile(r"<!-- (feature|open):([^ >]+) -->\n(.*?)\n<!-- /\1:\2 -->", re.S)
NO_NOTE_TYPES = {"docs", "chore", "test", "ci", "style", "refactor", "build"}
KEYS = {"feature", "status", "new", "plan"}


class Problem(Exception):
    pass


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def blocks(text):
    """{(kind, slug): inner text}"""
    return {(m[1], m[2]): m[3] for m in BLOCK.finditer(text)}


def doc_problems(text):
    """Markers that are not part of a well-formed block."""
    rest = BLOCK.sub("", text)
    return [f"the doc has a broken marker: {m}" for m in re.findall(r"<!-- /?(?:feature|open):\S+ -->", rest)]


def parse_note(path, text):
    """Return (meta, sections). Raise Problem with a plain message."""
    m = re.match(r"---\n(.*?)\n---\n(.*)\Z", text, re.S)
    if not m:
        raise Problem("needs front matter: a first line '---', the settings, then '---'")
    meta = {}
    for raw in m[1].split("\n"):
        line = re.sub(r"\s+#.*$", "", raw).strip()
        if not line:
            continue
        key, sep, val = line.partition(":")
        if not sep or key.strip() not in KEYS:
            raise Problem(f"front matter line not understood: '{raw.strip()}'")
        meta[key.strip()] = val.strip()
    feature = meta.get("feature", "")
    if not SLUG.fullmatch(feature):
        raise Problem("feature must be lowercase letters, digits and dashes")
    if feature != path.split("/")[-2]:
        raise Problem(f"feature '{feature}' does not match its folder '{path.split('/')[-2]}'")
    if meta.get("status") not in ("in-progress", "ready"):
        raise Problem("status must be in-progress or ready")
    if meta.get("new", "false") not in ("true", "false"):
        raise Problem("new must be true or false")
    sections, name = {}, None
    for line in m[2].split("\n"):
        h = re.match(r"## (.+?)\s*$", line)
        if h:
            name = h[1]
            sections[name] = []
        elif name:
            sections[name].append(line)
    sections = {k: "\n".join(v).strip() for k, v in sections.items()}
    if not sections.get("Line"):
        raise Problem("needs a '## Line' section with text")
    return meta, sections


def note_files():
    out = []
    if os.path.isdir(NOTES):
        for dirpath, _, files in os.walk(NOTES):
            out += [os.path.join(dirpath, f).replace(os.sep, "/") for f in files if f != ".gitkeep"]
    return sorted(out)


def load_notes():
    """(path, meta, sections) for every good note, plus a list of problems."""
    good, errs = [], []
    for p in note_files():
        depth = p[len(NOTES) + 1:].count("/")
        try:
            if depth != 1 or not p.endswith(".md"):
                raise Problem("notes must be handover/notes/<feature>/<name>.md")
            good.append((p, *parse_note(p, read(p))))
        except Problem as e:
            errs.append(f"{p}: {e}")
    return good, errs


def check(doc, base, title, skip):
    text = read(doc)
    errs = doc_problems(text)
    now = blocks(text)
    notes, bad = load_notes()
    errs += bad
    for p, meta, _ in notes:
        exists = ("feature", meta["feature"]) in now
        isnew = meta.get("new") == "true"
        if not exists and not isnew:
            errs.append(f"{p}: '{meta['feature']}' has no block in {doc}; add 'new: true' to the note")
        if exists and isnew:
            errs.append(f"{p}: '{meta['feature']}' already has a block in {doc}; remove 'new: true'")
    folding = os.environ.get("HANDOVER_FOLD") == "1"
    if not folding:
        old = git("show", f"{base}:{doc}")
        old = blocks(old) if old else {}
        for (kind, slug), inner in old.items():
            if (kind, slug) not in now:
                errs.append(f"{doc}: the {kind}:{slug} block was removed or renamed; only the fold may do that")
            elif now[(kind, slug)] != inner:
                errs.append(f"{doc}: the {kind}:{slug} block was edited; add a note in {NOTES}/{slug}/ instead")
        if old:
            for kind, slug in now.keys() - old.keys():
                errs.append(f"{doc}: the {kind}:{slug} block was added by hand; use a note with 'new: true'")
        changed = git("diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD", "--", NOTES)
        if changed is None:
            errs.append(f"could not compare with '{base}'; is it fetched?")
        elif not changed.strip() and not skip:
            kind = re.match(r"(\w+)(\([^)]*\))?!?:", title or "")
            if not (kind and kind[1] in NO_NOTE_TYPES):
                errs.append(f"this PR has no note; add one in {NOTES}/<feature>/ (or label the PR no-note)")
    return errs


def added_time(path):
    out = git("log", "--diff-filter=A", "--format=%ct", "--", path)
    times = [int(t) for t in (out or "").split()]
    return max(times) if times else float("inf")  # not committed yet = newest


def put(text, kind, slug, new, marker):
    """Replace, remove (new is None) or insert (before marker) one block."""
    old = blocks(text).get((kind, slug))
    start, end = f"<!-- {kind}:{slug} -->", f"<!-- /{kind}:{slug} -->"
    if old is not None:
        whole = f"{start}\n{old}\n{end}"
        return text.replace(whole + "\n", "", 1) if new is None else text.replace(whole, f"{start}\n{new}\n{end}", 1)
    if new is None:
        return text
    if marker not in text:
        raise Problem(f"the doc has no {marker} marker")
    return text.replace(marker, f"{start}\n{new}\n{end}\n{marker}", 1)


def fold(doc):
    text = read(doc)
    notes, _ = load_notes()
    by_feature = {}
    for n in notes:
        by_feature.setdefault(n[1]["feature"], []).append(n)
    done = []
    for feature in sorted(by_feature):
        newest = max(by_feature[feature], key=lambda n: (added_time(n[0]), n[0]))
        _, meta, sec = newest
        if meta["status"] != "ready":
            continue
        try:
            line = sec["Line"]
            if meta.get("plan"):
                line += f" Plan: {meta['plan']}."
            if ("feature", feature) not in blocks(text) and meta.get("new") != "true":
                raise Problem("no block in the doc and the note is not 'new: true'")
            new = put(text, "feature", feature, line, NEW_FEATURES)
            if "Open work" in sec:
                open_text = None if sec["Open work"].lower() == "none" else sec["Open work"]
                new = put(new, "open", feature, open_text, NEW_OPEN)
        except Problem as e:
            print(f"skipped {feature}: {e}", file=sys.stderr)
            continue
        text = new
        for p, _, _ in by_feature[feature]:
            os.remove(p)
        d = os.path.dirname(p)
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
        done.append(feature)
    if done:
        with open(doc, "w", encoding="utf-8") as f:
            f.write(text)
        print("Folded: " + ", ".join(done))
    else:
        print("nothing to fold")


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--doc", help="the handover doc (default: whichever of %s exists)" % " / ".join(DOCS))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", parents=[common], help="the PR gate")
    c.add_argument("--base", required=True, help="git ref of the target branch, e.g. origin/main")
    c.add_argument("--title", default="", help="the PR title")
    c.add_argument("--skip", action="store_true", help="the PR has the no-note label")
    sub.add_parser("fold", parents=[common], help="fold ready notes into the doc")
    a = ap.parse_args()
    doc = a.doc or next((d for d in DOCS if os.path.exists(d)), None)
    if not doc or not os.path.exists(doc):
        sys.exit("error: no handover doc found; use --doc")
    if a.cmd == "fold":
        fold(doc)
        return
    errs = check(doc, a.base, a.title, a.skip)
    for e in errs:
        print(f"::error::{e}" if os.environ.get("GITHUB_ACTIONS") else f"error: {e}")
    if errs:
        sys.exit(1)
    print("handover check passed")


if __name__ == "__main__":
    main()
