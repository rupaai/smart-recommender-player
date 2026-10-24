#!/usr/bin/env python3
"""Tests for handover.py. Script style: run it, it exits 1 if anything fails.
Each case builds a tiny git repo in a temp folder with a small doc and some notes."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = next(p for p in (HERE / "handover.py", HERE.parent / "scripts" / "handover.py") if p.exists())
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


DOC = """# Doc
## Open work
<!-- open:logs -->
- **Logs, next:** search.
<!-- /open:logs -->
<!-- new-open-work -->
- Workflow line.
## State
<!-- feature:files -->
- **Files**: old text.
<!-- /feature:files -->
<!-- feature:logs -->
- **Logs**: old logs.
<!-- /feature:logs -->
<!-- new-features -->
- Outside the blocks.
"""


def note(feature, status="ready", line="- **X**: new text.", extra="", opens=None, front=""):
    body = f"---\nfeature: {feature}\nstatus: {status}\n{extra}{front}---\n## Line\n{line}\n"
    if opens is not None:
        body += f"\n## Open work\n{opens}\n"
    return body + "\n## What changed\nDetails.\n"


class Repo:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        self.env.pop("HANDOVER_FOLD", None)
        self.t = 0
        self.git("init", "-q", "-b", "main")
        self.write("HANDOVER.md", DOC)
        self.commit("base", 1000)
        self.git("checkout", "-q", "-b", "pr")
        self.t = 2000

    def git(self, *a):
        return subprocess.run(["git", *a], cwd=self.dir, env=self.env, capture_output=True, text=True).stdout

    def write(self, rel, text):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg="c", when=None):
        self.t += 100
        when = when or self.t
        self.env["GIT_COMMITTER_DATE"] = self.env["GIT_AUTHOR_DATE"] = f"@{when} +0000"
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def add_note(self, feature, name, text, when=None):
        self.write(f"handover/notes/{feature}/{name}.md", text)
        self.commit("note", when)

    def run(self, *args, env=None):
        r = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.dir, capture_output=True, text=True,
                           env=dict(self.env, **(env or {})))
        return r.returncode, r.stdout + r.stderr

    def chk(self, title="feat: x", *extra, env=None):
        return self.run("check", "--base", "main", "--title", title, *extra, env=env)

    def doc(self):
        return (self.dir / "HANDOVER.md").read_text()

    def notes(self):
        return sorted(str(p.relative_to(self.dir)) for p in (self.dir / "handover/notes").rglob("*.md")) \
            if (self.dir / "handover/notes").exists() else []


def passes(name, repo, *a, **k):
    code, out = repo.chk(*a, **k)
    check(name, code == 0, out)


def fails(name, repo, word, *a, **k):
    code, out = repo.chk(*a, **k)
    check(name, code == 1 and word in out, f"code={code} out={out}")


# --- check ---
r = Repo(); r.add_note("files", "a", note("files", "in-progress"))
passes("valid note passes", r)

r = Repo(); r.add_note("files", "a", "no front matter\n## Line\nx\n")
fails("missing front matter fails", r, "front matter")

r = Repo(); r.add_note("files", "a", note("logs"))
fails("feature not matching folder fails", r, "does not match")

r = Repo(); r.add_note("Bad_Slug", "a", note("Bad_Slug"))
fails("bad slug fails", r, "lowercase")

r = Repo(); r.add_note("files", "a", note("files", "done"))
fails("bad status fails", r, "status")

r = Repo(); r.add_note("files", "a", note("files", line=""))
fails("empty Line fails", r, "Line")

r = Repo(); r.add_note("files", "a", note("files", front="colour: red\n"))
fails("unknown front matter key fails", r, "not understood")

r = Repo(); r.add_note("nope", "a", note("nope"))
fails("unknown feature without new fails", r, "new: true")

r = Repo(); r.add_note("nope", "a", note("nope", extra="new: true\n"))
passes("unknown feature with new passes", r)

r = Repo(); r.add_note("files", "a", note("files", extra="new: true\n"))
fails("new on an existing feature fails", r, "already has a block")

r = Repo(); r.write("HANDOVER.md", DOC.replace("old text", "edited")); r.commit()
fails("block edit fails", r, "feature:files")

r = Repo(); r.write("HANDOVER.md", DOC.replace("Outside the blocks", "edited")); r.commit()
passes("outside-block edit passes", r, "docs: x")
r.add_note("files", "a", note("files"))
passes("outside-block edit with a note passes", r)

r = Repo(); r.write("HANDOVER.md", DOC.replace("<!-- open:logs -->\n- **Logs, next:** search.\n<!-- /open:logs -->\n", "")); r.commit()
fails("removed block fails", r, "removed or renamed")

r = Repo(); r.write("HANDOVER.md", DOC.replace("old text", "edited")); r.commit()
passes("block edit passes while folding", r, env={"HANDOVER_FOLD": "1"})

r = Repo(); r.write("README.md", "x"); r.commit()
fails("missing note fails", r, "no note")
passes("docs: title needs no note", r, "docs: x")
passes("scoped ci(x): title needs no note", r, "ci(x): x")
passes("--skip needs no note", r, "feat: x", "--skip")

# --- fold ---
r = Repo()
r.add_note("files", "a", note("files", line="- **Files**: new files text.", opens="- **Files, next:** x."))
code, out = r.run("fold")
d = r.doc()
check("fold replaces the line", code == 0 and "new files text." in d and "old text" not in d, out)
check("fold adds a missing open block before its marker",
      "<!-- open:files -->\n- **Files, next:** x.\n<!-- /open:files -->\n<!-- new-open-work -->" in d, d)
check("fold deletes notes", r.notes() == [] and "Folded: files" in out, out)
check("fold leaves other text alone", "- Outside the blocks." in d and "old logs." in d)

r = Repo(); r.add_note("files", "a", note("files", extra="plan: homehub/files.md\n", line="- **Files**: two."))
r.run("fold")
check("fold adds the plan pointer", "- **Files**: two. Plan: homehub/files.md." in r.doc(), r.doc())

r = Repo(); r.add_note("logs", "a", note("logs", opens="none"))
r.run("fold")
check("fold removes the open block on none", "open:logs" not in r.doc() and "- **Logs, next:**" not in r.doc(), r.doc())

r = Repo(); r.add_note("logs", "a", note("logs", opens="- **Logs, next:** later."))
r.run("fold")
check("fold replaces an open block", "- **Logs, next:** later.\n<!-- /open:logs -->" in r.doc(), r.doc())

r = Repo(); r.add_note("data", "a", note("data", extra="new: true\n", line="- **Data**: brand new."))
r.run("fold")
check("fold inserts a new feature before the marker",
      "<!-- feature:data -->\n- **Data**: brand new.\n<!-- /feature:data -->\n<!-- new-features -->" in r.doc(), r.doc())
passes("check passes on the folded doc", r, "docs: x", env={"HANDOVER_FOLD": "1"})

r = Repo(); r.add_note("files", "a", note("files", "in-progress", line="- **Files**: wip."))
code, out = r.run("fold")
check("in-progress feature is not folded", r.doc() == DOC and len(r.notes()) == 1 and "nothing to fold" in out, out)

r = Repo()
r.add_note("files", "z-old", note("files", line="- **Files**: old note."), when=5000)
r.add_note("files", "a-new", note("files", "in-progress", line="- **Files**: newer wip."), when=6000)
r.run("fold")
check("newest note (in-progress) wins over an older ready one", r.doc() == DOC and len(r.notes()) == 2, r.doc())
r.add_note("files", "m", note("files", line="- **Files**: newest."), when=7000)
r.run("fold")
check("newest ready note wins and all notes go", "- **Files**: newest." in r.doc() and r.notes() == [], r.doc())

r = Repo(); r.add_note("files", "a", note("files", line="- **Files**: once."), when=5000)
r.run("fold"); first = r.doc()
code, out = r.run("fold")
check("fold twice changes nothing", code == 0 and r.doc() == first and "nothing to fold" in out, out)

print()
print("FAILED: " + ", ".join(failures) if failures else "all handover tests passed")
sys.exit(1 if failures else 0)
