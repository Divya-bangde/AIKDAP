"""Disposable-only 027→028→027→028 schema and legacy payload verification."""
import os
import subprocess
import sys
import time
import uuid

import psycopg
from psycopg.types.json import Jsonb
from check import run


def main():
    name = "aikdap-test-visualization-" + uuid.uuid4().hex[:10]
    created = False
    try:
        run(["docker","run","-d","--rm","--name",name,"-p","127.0.0.1::5432","-e","POSTGRES_USER=test","-e","POSTGRES_PASSWORD=test","-e","POSTGRES_DB=aikdap_test_visualization","pgvector/pgvector:pg17"],timeout=120)
        created = True
        port = run(["docker","port",name,"5432"],capture=True,timeout=10).rsplit(":",1)[1]
        deadline = time.monotonic()+30
        while True:
            try:
                run(["docker","exec",name,"pg_isready","-U","test"],capture=True,timeout=5)
                break
            except subprocess.CalledProcessError:
                if time.monotonic() >= deadline: raise TimeoutError("Disposable PostgreSQL did not start")
                time.sleep(.5)
        url = f"postgresql://test:test@127.0.0.1:{port}/aikdap_test_visualization?connect_timeout=5"
        env = {**os.environ,"DATABASE_URL":url.replace("postgresql://","postgresql+psycopg://",1),"SECRET_KEY":"isolated-migration-fixture","CELERY_BROKER_URL":"redis://127.0.0.1:1/0","CELERY_RESULT_BACKEND":"redis://127.0.0.1:1/0","LITELLM_LOCAL_MODEL_COST_MAP":"True"}
        def migrate(direction,revision): run([sys.executable,"-m","alembic",direction,revision],env=env,timeout=60)
        migrate("upgrade","027_embedding_spaces")
        uid,pid,rid=[uuid.uuid4() for _ in range(3)]
        original={"kind":"chart2d","data":[{"type":"bar","x":["A"],"y":[42]}]}
        with psycopg.connect(url,autocommit=True) as db:
            db.execute("INSERT INTO users(id,email,hashed_password,is_active) VALUES(%s,%s,'fixture',true)",(uid,"visualization-fixture@example.com"))
            db.execute("INSERT INTO projects(id,owner_id,name,project_type,status) VALUES(%s,%s,'Disposable fixture','research','active')",(pid,uid))
            db.execute("INSERT INTO research_runs(id,project_id,owner_id,query,status,include_assets,include_web,max_results,final_answer,visualization) VALUES(%s,%s,%s,'bar chart','completed',true,false,5,'Legacy answer',%s)",(rid,pid,uid,Jsonb(original)))
        migrate("upgrade","028_visualization_outcome")
        outcome={"status":"not_enough_data","reason":"Synthetic fixture","verification":"unverified","requested":{"type":"bar"},"repair_attempts":0}
        with psycopg.connect(url,autocommit=True) as db:
            assert db.execute("SELECT final_answer,visualization,visualization_outcome FROM research_runs WHERE id=%s",(rid,)).fetchone()==("Legacy answer",original,None)
            db.execute("UPDATE research_runs SET visualization_outcome=%s WHERE id=%s",(Jsonb(outcome),rid))
            assert db.execute("SELECT visualization_outcome FROM research_runs WHERE id=%s",(rid,)).fetchone()==(outcome,)
        print("PASS upgrade: existing answer/chart unchanged; legacy outcome NULL; new outcome JSONB round trips",flush=True)
        migrate("downgrade","027_embedding_spaces")
        with psycopg.connect(url) as db:
            assert db.execute("SELECT final_answer,visualization FROM research_runs WHERE id=%s",(rid,)).fetchone()==("Legacy answer",original)
            assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='research_runs' AND column_name='visualization_outcome'").fetchone()==(0,)
        print("PASS downgrade: only outcome column removed; legacy answer/chart preserved",flush=True)
        migrate("upgrade","028_visualization_outcome")
        with psycopg.connect(url) as db:
            assert db.execute("SELECT final_answer,visualization,visualization_outcome FROM research_runs WHERE id=%s",(rid,)).fetchone()==("Legacy answer",original,None)
        print("PASS re-upgrade: legacy outcome remains honestly NULL",flush=True)
    finally:
        if created: subprocess.run(["docker","rm","-f",name],timeout=20,check=False)


if __name__ == "__main__": main()
