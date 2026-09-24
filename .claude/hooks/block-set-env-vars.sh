#!/usr/bin/env bash
# PreToolUse hook, wired to the Bash tool via .claude/settings.json.
#
# Blocks any command that runs `gcloud run deploy` together with
# --set-env-vars (or --set-env-vars-file) — both REPLACE every env var on
# the service, wiping anything not explicitly listed. This is the exact
# mistake flagged in CLAUDE.md's "hard-won lessons" section, already made
# once in this project's history. --update-env-vars is unaffected and
# always allowed; it only changes the vars you list.
#
# Reads the PreToolUse JSON payload from stdin, checks the Bash command
# it's about to run. Exit 2 blocks the tool call and shows stderr to
# Claude; exit 0 lets it through untouched.

input="$(cat)"

command="$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get("tool_input", {}).get("command", ""))
except Exception:
    pass
' 2>/dev/null)"

if [ -z "$command" ]; then
  exit 0
fi

if echo "$command" | grep -Eq 'gcloud[[:space:]]+run[[:space:]]+deploy' \
   && echo "$command" | grep -Eq -- '--set-env-vars'; then
  {
    echo "Blocked: this command runs 'gcloud run deploy' with --set-env-vars (or --set-env-vars-file)."
    echo "That flag REPLACES every env var on the service — anything not explicitly listed is wiped."
    echo "Use --update-env-vars instead: it only changes the vars you list and leaves the rest untouched."
    echo "See CLAUDE.md's 'Hard-won lessons' — this exact mistake has already cost time once in this project."
    echo "If you genuinely need --set-env-vars for a deliberate full reset, run it yourself outside Claude Code."
  } >&2
  exit 2
fi

exit 0
