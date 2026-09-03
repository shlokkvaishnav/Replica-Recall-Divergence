#!/usr/bin/env bash
# SessionStart hook: switch the research/AGENT_PIPELINE.md role loop on by
# default. Emits additionalContext that tells the session to invoke /loop on
# its first turn, so the reviewer / implementer / researcher rotation runs
# without anyone typing the command. Nothing here changes what the roles do --
# that stays in research/AGENT_PIPELINE.md.
#
# Disable by removing the SessionStart entry from .claude/settings.json.

# Python does the JSON escaping (jq is not guaranteed on Windows); it is
# already required by research/check_index.py, so it is a dependency the
# repo has anyway.
python - <<'PY'
import json
ctx = """PIPELINE AUTOSTART (project hook, .claude/hooks/pipeline-autostart.sh): this repository runs the role-rotating research pipeline described in research/AGENT_PIPELINE.md, and it is switched on by default. On your FIRST turn of this session -- before addressing anything else, unless the user's message explicitly says not to start the pipeline -- invoke the `loop` skill with no interval (dynamic pacing) and this prompt:

  Run one iteration of research/AGENT_PIPELINE.md: read that file, execute its Role selection query list from the top, play the first role that fires (Reviewer first), chain until idle (condition 5), and log which condition fired. Re-derive all state from GitHub (gh), never from session memory. Never merge.

Then continue with whatever the user actually asked. If the user's message is itself a /loop invocation of this pipeline, do not start a second one."""
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
PY
