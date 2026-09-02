#!/usr/bin/env python3
"""Fail if research/README.md's experiment index has drifted from the tree.

Issue #22. The index is how a reader finds out what exists; when it drifts,
merged work becomes invisible. That happened three times in one session -- most
starkly with `qdrant_kill_scheduler/`, which was merged, validated, live tooling
that the index never mentioned -- and each time it was corrected by hand, which
lasts exactly until the next merge.

Two directions are checked, both mechanical:

  1. every research/<dir>/ on disk is named in the index
  2. every research/<dir>/ the index links to exists on disk

What this deliberately does NOT check is whether a row's *description* is
accurate. A row reading "Not started" for finished work -- the actual #15 defect
-- passes this check, because judging a description needs judgement and cannot
be mechanised. The honest claim is narrow: a directory becomes impossible to
omit, not impossible to describe wrongly.

Usage:
    python research/check_index.py           # exit 1 on drift
    python research/check_index.py --list    # show what was found, always exit 0
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "README.md")

# Directories that are not experiments and are not expected in the index.
# Kept explicit rather than pattern-matched: adding a name here is a decision
# someone makes on purpose, and it shows up in review as one.
EXEMPT = {
    "__pycache__",
    "proto",  # generated gRPC stubs, vendored under an experiment
}


def research_dirs():
    out = set()
    for name in sorted(os.listdir(HERE)):
        path = os.path.join(HERE, name)
        if not os.path.isdir(path) or name in EXEMPT or name.startswith("."):
            continue
        out.add(name)
    return out


def mentioned_dirs(text):
    """Every directory name the index refers to, matched loosely.

    Used only for the "exists but is not indexed" direction, where
    over-matching is the safe way to err: a stray match can only make the
    checker more forgiving about a directory being mentioned, never invent a
    failure.
    """
    found = set()
    for m in re.findall(r'\]\(([A-Za-z0-9_./-]+)/\)', text):
        found.add(m.strip("./").split("/")[0])
    for m in re.findall(r'`([A-Za-z0-9_-]+)/`', text):
        found.add(m)
    for m in re.findall(r'`([A-Za-z0-9_-]+)/[A-Za-z0-9_.-]+`', text):
        found.add(m)
    return found


def linked_dirs(text):
    """Directories the index actually LINKS to with a relative path.

    Used for the "index names something that does not exist" direction, which
    has to be strict. The loose matcher above also picks up branch names in
    prose -- `experiment/qdrant-kill-spacing` is a git branch, not a directory
    -- and flagging those as missing directories would be a false alarm, which
    is worse than no check at all: a checker that cries wolf gets switched off.
    Only a relative markdown link is a promise that a path exists.
    """
    found = set()
    for m in re.findall(r'\]\((?!https?:)([A-Za-z0-9_./-]+)/\)', text):
        part = m.strip("./").split("/")[0]
        if part not in ("..", "."):
            found.add(part)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true",
                    help="report and exit 0 regardless of drift")
    args = ap.parse_args()

    if not os.path.exists(INDEX):
        print(f"FAIL: {INDEX} does not exist")
        return 1
    text = open(INDEX, encoding="utf-8").read()

    on_disk = research_dirs()
    named = mentioned_dirs(text)
    linked = linked_dirs(text)

    missing = sorted(on_disk - named)            # exists, never mentioned
    dangling = sorted(linked - on_disk - EXEMPT)  # linked to, does not exist

    print(f"research/ directories on disk : {len(on_disk)}")
    for d in sorted(on_disk):
        print(f"    {'ok ' if d in named else 'MISSING FROM INDEX'} {d}")

    ok = True
    if missing:
        ok = False
        print("\nFAIL: these directories exist but the experiment index in "
              "research/README.md never names them:")
        for d in missing:
            print(f"    research/{d}/")
        print("\n  A reader following the index cannot find them. Add a row to "
              "the\n  experiment index -- investigation, location, type, status.")
    if dangling:
        ok = False
        print("\nFAIL: the index names these, but they do not exist:")
        for d in dangling:
            print(f"    research/{d}/")
        print("\n  Either the directory was moved and the row was not updated, "
              "or the\n  row describes something that was never committed.")

    if ok:
        print("\nOK: index and tree agree.")
    return 0 if (ok or args.list) else 1


if __name__ == "__main__":
    sys.exit(main())
