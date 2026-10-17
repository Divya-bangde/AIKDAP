"""Bounded offline checks or a disposable PostgreSQL/Redis integration suite.

From backend: ``.venv/Scripts/python.exe scripts/check.py unit`` or
``.venv/Scripts/python.exe scripts/check.py integration``. Extra pytest arguments
may follow the mode (for example ``integration tests/test_health_endpoint.py``).
Never connects to the application's configured database/Redis or model providers.
"""

import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, env=None, timeout=900, capture=False):
    result = subprocess.run(
        args, cwd=ROOT, env=env, timeout=timeout, check=True,
        text=True, capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"unit", "integration"}:
        raise SystemExit("Usage: python scripts/check.py {unit|integration} [pytest args]")
    env = os.environ.copy()
    env["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    for key in ("TEST_DATABASE_URL", "TEST_REDIS_URL", "TEST_LIVE_OLLAMA"):
        env.pop(key, None)
    pytest = [sys.executable, "-m", "pytest", "-q", "--tb=short"]
    if sys.argv[1] == "unit":
        run([*pytest, "-m", "not integration and not live_ollama", *sys.argv[2:]], env=env)
        return

    prefix = "aikdap-test-" + uuid.uuid4().hex[:10]
    containers = []
    try:
        for suffix, image, port, options in (
            ("pg", "pgvector/pgvector:pg17", "5432", [
                "-e", "POSTGRES_USER=test", "-e", "POSTGRES_PASSWORD=test",
                "-e", "POSTGRES_DB=aikdap_test",
            ]),
            ("redis", "redis:8-alpine", "6379", []),
        ):
            name = f"{prefix}-{suffix}"
            run(["docker", "run", "-d", "--rm", "--name", name,
                 "-p", f"127.0.0.1::{port}", *options, image], timeout=120)
            containers.append(name)
            mapped = run(["docker", "port", name, port], timeout=10, capture=True).rsplit(":", 1)[1]
            if suffix == "pg":
                env["TEST_DATABASE_URL"] = f"postgresql+psycopg://test:test@127.0.0.1:{mapped}/aikdap_test?connect_timeout=5"
            else:
                env["TEST_REDIS_URL"] = f"redis://127.0.0.1:{mapped}/0"
        deadline = time.monotonic() + 30
        while True:
            try:
                run(["docker", "exec", containers[0], "pg_isready", "-U", "test", "-d", "aikdap_test"], timeout=5, capture=True)
                break
            except subprocess.CalledProcessError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Disposable PostgreSQL did not become ready in 30 seconds")
                time.sleep(0.5)
        env.update(
            DATABASE_URL=env["TEST_DATABASE_URL"],
            SECRET_KEY="isolated-test-key-never-use-in-production",
            CELERY_BROKER_URL=env["TEST_REDIS_URL"],
            CELERY_RESULT_BACKEND=env["TEST_REDIS_URL"],
        )
        run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, timeout=60)
        run([*pytest, *sys.argv[2:]], env=env)
    finally:
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "-f", name], timeout=20, check=False)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
    except subprocess.TimeoutExpired:
        raise SystemExit("Check exceeded its bounded timeout")
