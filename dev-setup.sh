#!/usr/bin/env bash
# Run on each developer machine to point Claude Code at the shared collector.
# Usage: COLLECTOR_HOST=<ip-or-hostname> bash dev-setup.sh
#
# Then open a new shell (or `source ~/.zshrc`) and run claude normally.

set -euo pipefail

COLLECTOR_HOST="${COLLECTOR_HOST:-localhost}"
SHELL_RC="${HOME}/.zshrc"
[[ "$SHELL" == *"bash"* ]] && SHELL_RC="${HOME}/.bashrc"

BLOCK="
# Claude Code OTEL telemetry — added by dev-setup.sh
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://${COLLECTOR_HOST}:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
# Critical: cumulative so short sessions don't vanish before Prometheus scrapes
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
# Uncomment to include prompt text in logs (privacy: off by default)
# export OTEL_LOG_USER_PROMPTS=1
# export OTEL_LOG_TOOL_DETAILS=1
"

if grep -q "CLAUDE_CODE_ENABLE_TELEMETRY" "$SHELL_RC" 2>/dev/null; then
  echo "Already configured in $SHELL_RC — check for stale values if changing COLLECTOR_HOST."
else
  echo "$BLOCK" >> "$SHELL_RC"
  echo "Written to $SHELL_RC. Open a new shell or: source $SHELL_RC"
fi

echo ""
echo "Collector endpoint: http://${COLLECTOR_HOST}:4318"
echo "Test (after new shell): OTEL_METRICS_EXPORTER=console claude -p 'hi' 2>&1 | grep -i 'otel\|metric'"
