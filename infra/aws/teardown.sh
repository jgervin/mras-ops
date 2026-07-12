#!/usr/bin/env bash
# TODO-2: safe shutdown + cost check for the MRAS venue box.
#
# Finds every instance tagged mras:managed=true (any non-terminated state),
# shows uptime and an estimated cost, asks for confirmation, terminates, then
# warns about any unattached EBS volumes still billing in the region.
#
# Optional env:
#   DRY_RUN=1          print the terminate command instead of executing it
#   FORCE=1            skip the confirmation prompt
#   ON_DEMAND_RATE     $/hr used for the estimate (default 0.526, g4dn.xlarge
#                      us-east-1 on-demand; spot instances cost less)
#   AWS_REGION         else your aws CLI default region
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

command -v aws >/dev/null 2>&1 || die "aws CLI not found — install AWS CLI v2"
ON_DEMAND_RATE="${ON_DEMAND_RATE:-0.526}"
DRY_RUN="${DRY_RUN:-0}"

rows="$(aws ec2 describe-instances \
  --filters "Name=tag:mras:managed,Values=true" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].[InstanceId,LaunchTime,State.Name]' \
  --output text)"

if [[ -z "$rows" || "$rows" == "None" ]]; then
  echo "no mras-managed instances found — nothing to terminate"
  exit 0
fi

ids=()
while IFS=$'\t' read -r id launch_time state; do
  [[ -n "$id" ]] || continue
  read -r hours cost < <(python3 - "$launch_time" "$ON_DEMAND_RATE" <<'PY'
import datetime
import sys

launched = datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
hours = max((datetime.datetime.now(datetime.timezone.utc) - launched).total_seconds() / 3600, 0)
print(f"{hours:.1f} {hours * float(sys.argv[2]):.2f}")
PY
)
  echo "$id  state=$state  up ${hours}h  est cost \$$cost (at \$$ON_DEMAND_RATE/hr on-demand; spot bills less)"
  ids+=("$id")
done <<< "$rows"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN> aws ec2 terminate-instances --instance-ids ${ids[*]}"
  echo "DRY_RUN complete — nothing was terminated."
  exit 0
fi

if [[ "${FORCE:-0}" != "1" ]]; then
  read -r -p "terminate ${#ids[@]} instance(s)? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "aborted"; exit 1; }
fi

aws ec2 terminate-instances --instance-ids "${ids[@]}" >/dev/null
echo "terminating ${ids[*]} — waiting ..."
aws ec2 wait instance-terminated --instance-ids "${ids[@]}"
echo "terminated: ${ids[*]} (root EBS volumes delete on termination)"

# Cost check: anything still billing? The root volume is DeleteOnTermination,
# so orphans are unexpected — but list unattached volumes in the region so a
# leak never bills silently (some may be unrelated to MRAS; check before deleting).
orphans="$(aws ec2 describe-volumes --filters "Name=status,Values=available" \
  --query 'Volumes[].VolumeId' --output text)"
if [[ -n "$orphans" && "$orphans" != "None" ]]; then
  echo "WARNING: unattached EBS volumes still billing in this region: $orphans"
  echo "         delete (after checking!) with: aws ec2 delete-volume --volume-id <id>"
else
  echo "no unattached EBS volumes left in this region"
fi
