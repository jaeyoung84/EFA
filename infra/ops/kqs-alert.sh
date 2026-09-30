#!/bin/sh
# Report a failed KQS unit to the journal and, if configured, to an SNS topic. No secrets or data.
set -u
unit="${1:-unknown}"
[ -r /srv/kqs/secrets/ops.env ] && . /srv/kqs/secrets/ops.env
message="KQS unit failed: ${unit} on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ). See: journalctl -u ${unit}"
logger -t kqs-alert "$message"
if [ -n "${KQS_ALERT_SNS_TOPIC_ARN:-}" ]; then
  aws sns publish --topic-arn "$KQS_ALERT_SNS_TOPIC_ARN" --subject "KQS alert" --message "$message" >/dev/null
fi
