"""TODO-2: AWS GPU rental profile (/Users/jn/code/mras-ops/infra/aws/).

No AWS account is available in CI/dev, so these tests exercise launch.sh and
teardown.sh in DRY_RUN mode against a fake `aws` CLI placed first on PATH,
plus validate the docker-compose.aws.yml override with `docker compose config`.
The live launch path is covered by the owner runbook in infra/aws/README.md.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra" / "aws"

# Answers only the read-only lookups the scripts make; ignores --query/--output
# flags and returns the text-form value each lookup expects. Mutating calls are
# never reached in DRY_RUN mode (the scripts must print them instead).
FAKE_AWS = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_AWS_LOG"
case "$1 $2" in
  "ec2 describe-images") echo "ami-fake1234567890abc" ;;
  "ec2 describe-security-groups") echo "${FAKE_SG_ID:-None}" ;;
  "ec2 describe-instances")
    if [[ "${FAKE_RUNNING_INSTANCE:-}" == "1" ]]; then
      printf 'i-0fake1234\\t2026-07-11T00:00:00+00:00\\trunning\\n'
    fi ;;
  "ec2 describe-volumes") : ;;
  "sts get-caller-identity") echo "123456789012" ;;
  *) echo "fake-aws: unexpected mutating call: $*" >&2; exit 42 ;;
esac
"""


@pytest.fixture
def aws_env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "aws"
    fake.write_text(FAKE_AWS)
    fake.chmod(0o755)
    log = tmp_path / "aws-calls.log"
    log.touch()
    env = os.environ.copy()
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["FAKE_AWS_LOG"] = str(log)
    env["AWS_REGION"] = "us-east-1"
    env["DRY_RUN"] = "1"
    return env


def run_script(name, env, **extra):
    e = dict(env)
    e.update({k: str(v) for k, v in extra.items()})
    return subprocess.run(
        ["bash", str(INFRA / name)], env=e, capture_output=True, text=True
    )


def test_launch_dry_run_builds_on_demand_command(aws_env):
    r = run_script(
        "launch.sh", aws_env, KEY_NAME="venue-key",
        ALLOW_CIDR="203.0.113.7/32", FAKE_SG_ID="sg-fakeexist123",
    )
    assert r.returncode == 0, r.stderr
    assert "run-instances" in r.stdout
    assert "g4dn.xlarge" in r.stdout
    assert "ami-fake1234567890abc" in r.stdout
    assert "sg-fakeexist123" in r.stdout
    assert "MarketType=spot" not in r.stdout


def test_launch_dry_run_spot_toggle(aws_env):
    r = run_script(
        "launch.sh", aws_env, KEY_NAME="venue-key",
        ALLOW_CIDR="203.0.113.7/32", FAKE_SG_ID="sg-fakeexist123", SPOT="1",
    )
    assert r.returncode == 0, r.stderr
    assert "MarketType=spot" in r.stdout


def test_launch_creates_security_group_when_missing(aws_env):
    r = run_script(
        "launch.sh", aws_env, KEY_NAME="venue-key", ALLOW_CIDR="203.0.113.7/32",
    )
    assert r.returncode == 0, r.stderr
    assert "create-security-group" in r.stdout
    # SSH plus the composer WS port the venue displays connect to
    assert "22" in r.stdout
    assert "8002" in r.stdout
    assert "203.0.113.7/32" in r.stdout


def test_launch_requires_key_name_and_allow_cidr(aws_env):
    r = run_script("launch.sh", aws_env, ALLOW_CIDR="203.0.113.7/32")
    assert r.returncode != 0
    assert "KEY_NAME" in r.stderr
    r = run_script("launch.sh", aws_env, KEY_NAME="venue-key")
    assert r.returncode != 0
    assert "ALLOW_CIDR" in r.stderr


def test_launch_aborts_when_instance_already_running(aws_env):
    r = run_script(
        "launch.sh", aws_env, KEY_NAME="venue-key",
        ALLOW_CIDR="203.0.113.7/32", FAKE_RUNNING_INSTANCE="1",
    )
    assert r.returncode != 0
    assert "already" in (r.stdout + r.stderr).lower()


def test_teardown_dry_run_reports_instance_and_cost(aws_env):
    r = run_script("teardown.sh", aws_env, FAKE_RUNNING_INSTANCE="1")
    assert r.returncode == 0, r.stderr
    assert "i-0fake1234" in r.stdout
    assert "terminate-instances" in r.stdout
    assert re.search(r"\$\d", r.stdout), "expected a cost estimate in output"


def test_teardown_is_a_safe_noop_without_instances(aws_env):
    r = run_script("teardown.sh", aws_env)
    assert r.returncode == 0, r.stderr
    assert "no " in r.stdout.lower()
    assert "terminate-instances" not in r.stdout


def test_shell_scripts_pass_bash_syntax_check():
    scripts = sorted(INFRA.glob("*.sh"))
    assert scripts, f"no shell scripts found in {INFRA}"
    for script in scripts:
        r = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True
        )
        assert r.returncode == 0, f"{script}: {r.stderr}"


def test_compose_aws_override_adds_gpu_and_cuda_to_vision():
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    r = subprocess.run(
        [
            "docker", "compose",
            "-f", "docker-compose.yml",
            "-f", "infra/aws/docker-compose.aws.yml",
            "--profile", "docker-vision",
            "config",
        ],
        cwd=REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "mras-vision" in r.stdout
    assert "nvidia" in r.stdout, "vision service should reserve the NVIDIA GPU"
    assert "and-cuda" in r.stdout, "CUDA-enabled TF should be in the AWS build"
