"""Check Phase 1 migrations against representative data in disposable pgvector.

Run from backend: .venv/Scripts/python.exe scripts/verify_phase1_migrations.py
Never accepts a database URL or uses the application's configured database.
Downgrade deliberately discards comparison/cache/rebuild records; source rows
and the original legacy vector must survive upgrade, downgrade and re-upgrade.
"""

import os
import subprocess
import sys
import time
import uuid

import psycopg
from check import run
from psycopg.types.json import Jsonb

BASELINE = "d4e5f6a7b8c0"


def snapshot(connection):
    return connection.execute(
        """SELECT u.email, p.name, a.title, a.checksum, a.version,
                  a.metadata, a.source_mix, k.id, k.asset_id, k.project_id,
                  k.chunk_index, k.content, k.embedding::text,
                  k.embedding_status, k.page_number, k.section
           FROM knowledge_chunks k
           JOIN assets a ON a.id = k.asset_id
           JOIN projects p ON p.id = k.project_id
           JOIN users u ON u.id = p.owner_id"""
    ).fetchall()


def main():
    name = "aikdap-test-migration-" + uuid.uuid4().hex[:10]
    created = False
    try:
        run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "-p",
                "127.0.0.1::5432",
                "-e",
                "POSTGRES_USER=test",
                "-e",
                "POSTGRES_PASSWORD=test",
                "-e",
                "POSTGRES_DB=aikdap_test_migration",
                "pgvector/pgvector:pg17",
            ],
            timeout=120,
        )
        created = True
        port = run(["docker", "port", name, "5432"], capture=True, timeout=10).rsplit(
            ":", 1
        )[1]
        deadline = time.monotonic() + 30
        while True:
            try:
                run(
                    ["docker", "exec", name, "pg_isready", "-U", "test"],
                    capture=True,
                    timeout=5,
                )
                break
            except subprocess.CalledProcessError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Disposable PostgreSQL unavailable after 30 seconds"
                    )
                time.sleep(0.5)
        url = f"postgresql://test:test@127.0.0.1:{port}/aikdap_test_migration?connect_timeout=5"
        env = os.environ.copy()
        env.update(
            DATABASE_URL=url.replace("postgresql://", "postgresql+psycopg://", 1),
            SECRET_KEY="isolated-migration-key-never-use-in-production",
            CELERY_BROKER_URL="redis://127.0.0.1:1/0",
            CELERY_RESULT_BACKEND="redis://127.0.0.1:1/0",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
        )

        def migrate(direction, revision):
            run(
                [sys.executable, "-m", "alembic", direction, revision],
                env=env,
                timeout=60,
            )

        migrate("upgrade", BASELINE)
        user_id, project_id, asset_id, chunk_id = [uuid.uuid4() for _ in range(4)]
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("SET statement_timeout = '10s'")
            connection.execute(
                "INSERT INTO users (id, email, hashed_password, is_active) "
                "VALUES (%s,%s,%s,true)",
                (user_id, "migration-fixture@example.com", "offline-not-a-real-hash"),
            )
            connection.execute(
                """INSERT INTO projects (id,owner_id,name,project_type,status)
                   VALUES (%s,%s,'Existing research project','research','active')""",
                (project_id, user_id),
            )
            connection.execute(
                """INSERT INTO assets
                   (id,project_id,owner_id,title,asset_type,status,source,
                   mime_type,file_name,file_extension,file_size,storage_path,checksum,version,
                   tags,metadata,ai_profile,processing_status)
                   VALUES (%s,%s,%s,'Existing paper','document','active',
                   'upload','text/plain',
                   'paper.txt','txt',46,'legacy/paper.txt',%s,3,%s,%s,%s,'completed')""",
                (
                    asset_id,
                    project_id,
                    user_id,
                    "a" * 64,
                    [],
                    Jsonb({"provenance": "legacy uploaded paper"}),
                    Jsonb({}),
                ),
            )
            vector = "[" + ",".join(["1"] + ["0"] * 1023) + "]"
            connection.execute(
                """INSERT INTO knowledge_chunks
                   (id,project_id,asset_id,chunk_index,content,
                   embedding_status,embedding_provider,embedding,page_number,section)
                   VALUES (%s,%s,%s,0,'Real legacy passage with original provenance.',
                   'completed','local',%s::vector,2,'Results')""",
                (chunk_id, project_id, asset_id, vector),
            )
            original = snapshot(connection)
            assert len(original) == 1

        migrate("upgrade", "head")
        with psycopg.connect(url, autocommit=True) as connection:
            assert snapshot(connection) == original, (
                "Upgrade altered legacy source data/vector"
            )
            row = connection.execute(
                """SELECT k.embedding_space_id,k.embedding_space,
                          p.active_embedding_space_id,
                          vector_dims(k.embedding)
                   FROM knowledge_chunks k JOIN projects p ON p.id=k.project_id"""
            ).fetchone()
            assert row == (None, None, None, 1024), (
                "Legacy vector incorrectly verified or changed"
            )
            assert connection.execute(
                "SELECT count(*) FROM embedding_rebuilds"
            ).fetchone() == (0,)
            connection.execute(
                """INSERT INTO document_understanding_cache
                   (id,asset_id,cache_key,source_version,understanding)
                   VALUES (%s,%s,%s,%s,%s)""",
                (
                    uuid.uuid4(),
                    asset_id,
                    "b" * 64,
                    Jsonb({"checksum": "a" * 64}),
                    Jsonb({}),
                ),
            )
            connection.execute(
                """INSERT INTO cross_paper_artifacts
                   (id,owner_id,project_id,primary_asset_id,result)
                   VALUES (%s,%s,%s,%s,%s)""",
                (
                    uuid.uuid4(),
                    user_id,
                    project_id,
                    asset_id,
                    Jsonb({"state": "insufficient_evidence"}),
                ),
            )
        print(
            "PASS upgrade: source identities/provenance/vector preserved; "
            "legacy space stays NULL",
            flush=True,
        )

        migrate("downgrade", BASELINE)
        with psycopg.connect(url) as connection:
            assert snapshot(connection) == original, (
                "Downgrade altered original source data/vector"
            )
            assert connection.execute(
                """SELECT to_regclass('cross_paper_artifacts'),
                          to_regclass('document_understanding_cache'),
                          to_regclass('embedding_rebuilds'),to_regclass('embedding_rebuild_items')"""
            ).fetchone() == (None, None, None, None)
        print(
            "PASS downgrade: only Phase 1 artifacts/cache/rebuild schema removed; "
            "source data survives",
            flush=True,
        )

        migrate("upgrade", "head")
        with psycopg.connect(url) as connection:
            assert snapshot(connection) == original, (
                "Re-upgrade altered original source data/vector"
            )
            for table in (
                "cross_paper_artifacts",
                "document_understanding_cache",
                "embedding_rebuilds",
                "embedding_rebuild_items",
            ):
                # Table identifiers are fixed internal names, never user inputs.
                assert connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone() == (0,)
        print(
            "PASS re-upgrade: source data survives; "
            "discarded Phase 1 artifacts are not resurrected",
            flush=True,
        )
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", name], timeout=20, check=False)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
    except subprocess.TimeoutExpired:
        raise SystemExit("Migration check exceeded its bounded timeout")
