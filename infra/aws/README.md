# AWS GPU rental profile (TODO-2)

Reproducible launch profile for running the MRAS stack on a rented GPU instance
(default **g4dn.xlarge**: NVIDIA T4, 4 vCPU, 16 GB) for Phase 1 multi-camera
venue events. Rent hourly, run the event, terminate.

> **Verification status (honest):** built and reviewed without an AWS account.
> `launch.sh`/`teardown.sh` are exercised by `tests/test_aws_profile.py` in
> `DRY_RUN` mode against a fake `aws` CLI, pass `bash -n` + shellcheck, and the
> compose override is validated with `docker compose config`. The first real
> launch (below) is the live E2E — expect to babysit it once.

## Prerequisites

- AWS account with **g4dn.xlarge quota** (Service Quotas → EC2 → "Running
  On-Demand G and VT instances" ≥ 4 vCPUs; separate quota for spot).
- AWS CLI v2 configured (`aws configure`), default region set or `AWS_REGION` exported.
- A **default VPC** in the region (launch.sh passes no vpc/subnet ids; that's the
  out-of-the-box account state — accounts with the default VPC deleted need edits).
- An EC2 **key pair** in that region (`aws ec2 create-key-pair --key-name mras-venue ...`).
- Locally: the four sibling repos under one directory (`mras-ops`, `mras-vision`,
  `mras-composer`, `mras-overlays`) and a filled-in `mras-ops/.env`.

## Quick start

```bash
cd mras-ops/infra/aws

# 1. launch (on-demand; add SPOT=1 for ~60-70% cheaper, reclaimable — rehearsals only)
KEY_NAME=mras-venue ALLOW_CIDR="$(curl -s https://checkip.amazonaws.com)/32" ./launch.sh

# (DRY_RUN=1 with the same env prints every mutating AWS command without creating anything)

# 2-5. follow the "Next steps" the script prints: ssh + nvidia-smi, rsync repos,
#      scp .env, compose up with the AWS override, transfer enrolled data (below)

# 6. after the event
./teardown.sh        # shows uptime + est cost, confirms, terminates, checks for orphaned EBS
```

`ALLOW_CIDR` must cover **both** the operator laptop and the venue network the
display kiosks egress from (they connect to composer `:8002`). If those differ,
add a second ingress rule for the venue CIDR in the `mras-venue` security group.

Bring the stack up on the box with the GPU override:

```bash
cd ~/mras/mras-ops
docker compose -f docker-compose.yml -f infra/aws/docker-compose.aws.yml \
  --profile docker-vision up -d --build
```

Verify the GPU is actually used (T4 shows up in all three):

```bash
nvidia-smi
docker compose -f docker-compose.yml -f infra/aws/docker-compose.aws.yml \
  --profile docker-vision exec mras-vision \
  python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
docker compose -f docker-compose.yml -f infra/aws/docker-compose.aws.yml \
  --profile docker-vision exec mras-vision \
  python -c "import torch; print(torch.cuda.is_available())"
```

## Estimated cost per 4-hour event

| Item | On-demand | Spot |
|---|---|---|
| g4dn.xlarge, 5 h (setup + event + teardown buffer) | $2.63 (@ $0.526/hr, us-east-1) | ~$0.80–1.30 |
| 100 GB gp3 root volume (billed while instance exists, ~1 day) | ~$0.27 | ~$0.27 |
| Data transfer out (dashboard + clips to venue, ~2 GB) | ~$0.18 | ~$0.18 |
| **Total** | **≈ $3** | **≈ $1.30–1.80** |

Rates drift — check the console for your region. **Use on-demand for paid
events**: spot can be reclaimed with 2 minutes' notice, mid-event. `teardown.sh`
prints uptime × on-demand rate as a sanity check before terminating.

## Transferring enrolled data

Enrolled identities live in two stores; both transfer in minutes. Run the
stack on the box first (fresh Postgres/Qdrant volumes get migrations 001–028
automatically via initdb).

**1. Qdrant face embeddings** (`mras_embeddings`, 512-dim) via snapshot:

```bash
# on the laptop (local stack running)
curl -X POST http://localhost:6333/collections/mras_embeddings/snapshots
# -> {"result":{"name":"<snap>.snapshot",...}}; download it:
curl -o mras_embeddings.snapshot \
  http://localhost:6333/collections/mras_embeddings/snapshots/<snap>.snapshot
scp mras_embeddings.snapshot ubuntu@$IP:

# on the box
curl -X POST 'http://localhost:6333/collections/mras_embeddings/snapshots/upload?priority=snapshot' \
  -F 'snapshot=@/home/ubuntu/mras_embeddings.snapshot'
```

**2. Postgres profiles** (and any other seed data you want, e.g. `ads`):

```bash
# on the laptop
docker exec mras-ops-postgres-1 pg_dump -U mras -d mras --data-only \
  -t subject_profiles > subject_profiles.sql          # add: -t ads  for the ad catalog
scp subject_profiles.sql ubuntu@$IP:

# on the box
docker exec -i mras-ops-postgres-1 psql -U mras -d mras < subject_profiles.sql
```

Ad media in `mras-ops/assets/` rides along with the repo rsync. Register the
venue's cameras/screens on the box afterwards via the God View `/fleet` page or
ops-api — registry rows are venue-specific; don't copy the dev ones.

**3. Secrets:** `scp mras-ops/.env ubuntu@$IP:mras/mras-ops/.env` — never bake
keys into an AMI or user-data. Redis stays loopback-only (base compose binds
`127.0.0.1`); the security group opens nothing but SSH + the app ports, and
only to `ALLOW_CIDR`.

## What the pieces do

- **`launch.sh`** — refuses to double-launch (tag `mras:managed=true`), resolves
  the newest *Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*
  (driver + Docker + NVIDIA container toolkit preinstalled), creates/reuses the
  `mras-venue` security group scoped to `ALLOW_CIDR`, launches with a 100 GB gp3
  root volume (`DeleteOnTermination`), optional `SPOT=1`, waits for `running`,
  prints the runbook with the real IP.
- **`docker-compose.aws.yml`** — overrides only `mras-vision`: CUDA-enabled
  build (`tensorflow[and-cuda]`; plain Linux TF wheels can't see the GPU) and an
  NVIDIA device reservation. Everything else runs exactly as the base compose.
- **`teardown.sh`** — lists mras-managed instances with uptime + estimated cost,
  confirms (skip with `FORCE=1`), terminates, waits, then warns about any
  unattached EBS volumes still billing in the region. The `mras-venue` security
  group is deliberately left in place (free, reused by the next launch).

## Known gaps (deliberate, Phase-1 scope)

- **No venue camera feed into the cloud box yet.** Vision only captures via a
  local device index (`cv2.VideoCapture(CAM_INDEX)`); `cameras.stream_url`
  (RTSP) exists in the schema but is not consumed. On EC2 there is no local
  camera, so the capture task fails gracefully while `/enroll`, `/health`, and
  the trigger pipeline still work — the box is fully exercisable via API today.
  RTSP ingest is filed as mras-vision#38.
- `DEEPFACE_BACKEND` is set to `cuda` for consistency but **no code reads it** —
  TF/torch auto-detect the GPU. If GPU utilization is zero, debug with the
  verify commands above, not that variable.
- One instance per account/region by design (the double-launch guard). Multi-box
  events would need per-event tags — out of scope until needed.
