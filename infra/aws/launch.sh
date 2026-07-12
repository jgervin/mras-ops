#!/usr/bin/env bash
# TODO-2: one-command launch of a GPU venue box (default g4dn.xlarge) for
# MRAS Phase 1 multi-camera events. Full runbook: infra/aws/README.md.
#
# Required env:
#   KEY_NAME     name of an existing EC2 key pair in the target region
#   ALLOW_CIDR   CIDR allowed to reach SSH + app ports (e.g. 203.0.113.7/32)
# Optional env:
#   INSTANCE_TYPE=g4dn.xlarge   SPOT=0|1 (spot is ~60-70% cheaper, reclaimable)
#   VOLUME_GB=100               AMI_ID=<override the DLAMI lookup>
#   SG_NAME=mras-venue          AWS_REGION (else your aws CLI default region)
#   DRY_RUN=0|1
#
# DRY_RUN=1 performs only read-only lookups and PRINTS every mutating command
# (create-security-group / authorize-security-group-ingress / run-instances)
# instead of executing it. Nothing is created and nothing bills.
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

command -v aws >/dev/null 2>&1 || die "aws CLI not found — install AWS CLI v2"

KEY_NAME="${KEY_NAME:-}"
[[ -n "$KEY_NAME" ]] || die "KEY_NAME is required (an existing EC2 key pair name)"
ALLOW_CIDR="${ALLOW_CIDR:-}"
[[ -n "$ALLOW_CIDR" ]] || die "ALLOW_CIDR is required (e.g. \$(curl -s https://checkip.amazonaws.com)/32)"

INSTANCE_TYPE="${INSTANCE_TYPE:-g4dn.xlarge}"
SPOT="${SPOT:-0}"
VOLUME_GB="${VOLUME_GB:-100}"
SG_NAME="${SG_NAME:-mras-venue}"
DRY_RUN="${DRY_RUN:-0}"
TAG_NAME="mras-venue"
# SSH + the host-published MRAS ports (vision api, composer WS for the venue
# displays, ops-api, ops-frontend). Postgres/Qdrant/Redis stay unreachable:
# the security group simply never opens them.
INGRESS_PORTS=(22 8001 8002 8080 3000)

# ── guard: refuse a second box (double-spend protection) ─────────────────────
existing="$(aws ec2 describe-instances \
  --filters "Name=tag:mras:managed,Values=true" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
if [[ -n "$existing" && "$existing" != "None" ]]; then
  die "an mras-managed instance already exists ($existing) — run teardown.sh first"
fi

# ── AMI: newest Deep Learning Base GPU AMI (Ubuntu 22.04) ────────────────────
# Ships the NVIDIA driver, Docker, and the NVIDIA container toolkit, so
# user-data stays tiny and boot-to-ready is minutes, not a driver install.
if [[ -z "${AMI_ID:-}" ]]; then
  AMI_ID="$(aws ec2 describe-images --owners amazon \
    --filters "Name=name,Values=Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*" \
              "Name=state,Values=available" \
    --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)"
fi
[[ -n "$AMI_ID" && "$AMI_ID" != "None" ]] \
  || die "could not resolve a Deep Learning Base GPU AMI in this region — set AMI_ID explicitly"
echo "AMI: $AMI_ID"

# ── security group: reuse by name, else create + open ports to ALLOW_CIDR ────
sg_id="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text)"
if [[ -z "$sg_id" || "$sg_id" == "None" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "DRY_RUN> aws ec2 create-security-group --group-name $SG_NAME" \
         "--description 'MRAS venue box: SSH + app ports from ALLOW_CIDR only'"
    sg_id="sg-DRYRUN"
  else
    sg_id="$(aws ec2 create-security-group --group-name "$SG_NAME" \
      --description "MRAS venue box: SSH + app ports from ALLOW_CIDR only" \
      --query 'GroupId' --output text)"
  fi
  for port in "${INGRESS_PORTS[@]}"; do
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "DRY_RUN> aws ec2 authorize-security-group-ingress --group-id $sg_id" \
           "--protocol tcp --port $port --cidr $ALLOW_CIDR"
    else
      aws ec2 authorize-security-group-ingress --group-id "$sg_id" \
        --protocol tcp --port "$port" --cidr "$ALLOW_CIDR" >/dev/null
    fi
  done
else
  echo "security group: reusing $SG_NAME ($sg_id) — existing ingress rules are kept as-is"
fi

# ── user-data: DLAMI has driver/docker/toolkit; just ensure compose v2 ───────
user_data_file="$(mktemp)"
trap 'rm -f "$user_data_file"' EXIT
cat > "$user_data_file" <<'USERDATA'
#!/usr/bin/env bash
set -euo pipefail
if ! docker compose version >/dev/null 2>&1; then
  apt-get update -y && apt-get install -y docker-compose-plugin || {
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" \
      -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  }
fi
mkdir -p /home/ubuntu/mras && chown ubuntu:ubuntu /home/ubuntu/mras
USERDATA

# ── run-instances ─────────────────────────────────────────────────────────────
args=(ec2 run-instances
  --image-id "$AMI_ID"
  --instance-type "$INSTANCE_TYPE"
  --key-name "$KEY_NAME"
  --security-group-ids "$sg_id"
  --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$VOLUME_GB,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]"
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG_NAME},{Key=mras:managed,Value=true}]"
  --user-data "file://$user_data_file"
  --count 1)
if [[ "$SPOT" == "1" ]]; then
  args+=(--instance-market-options
    "MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}")
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN> aws ${args[*]}"
  echo "DRY_RUN complete — nothing was created."
  exit 0
fi

instance_id="$(aws "${args[@]}" --query 'Instances[0].InstanceId' --output text)"
echo "launched $instance_id ($INSTANCE_TYPE, spot=$SPOT) — waiting for 'running' ..."
aws ec2 wait instance-running --instance-ids "$instance_id"
ip="$(aws ec2 describe-instances --instance-ids "$instance_id" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"

cat <<NEXT

instance $instance_id is running at $ip
(user-data may take another 1-2 min; billing has started — see README cost table)

Next steps (full runbook: infra/aws/README.md):
  1. ssh -i <your-key.pem> ubuntu@$ip            # verify GPU: nvidia-smi
  2. from your laptop, sync the repos (sibling layout matters):
       rsync -az --exclude .git --exclude .venv --exclude node_modules --exclude .claude \\
         ~/code/mras-ops ~/code/mras-vision ~/code/mras-composer ~/code/mras-overlays \\
         ubuntu@$ip:mras/
  3. scp ~/code/mras-ops/.env ubuntu@$ip:mras/mras-ops/.env
  4. on the box:
       cd ~/mras/mras-ops && docker compose -f docker-compose.yml \\
         -f infra/aws/docker-compose.aws.yml --profile docker-vision up -d --build
  5. transfer enrolled data (Qdrant snapshot + subject_profiles) — see README
  6. when the event is over: infra/aws/teardown.sh
NEXT
