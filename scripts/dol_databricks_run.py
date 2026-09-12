"""
Push the DOL Parquet to Databricks, run the Spark job, pull the summary back.

The whole round trip in one command, so a quarterly refresh is one step in a
workflow rather than four things to remember in order.

Push rather than pull, deliberately. Free Edition restricts outbound access to a
set of trusted domains and does not document whether an external S3 bucket is
among them, so nothing here depends on the cluster reaching AWS. It is ~30 MB
either way; the 576 MB of source XLSX stays local because Spark cannot read XLSX
at all, which is why the Parquet conversion exists.

Three Free Edition constraints are baked in, each found by hitting it:

  - A job cannot execute a python_file from a Unity Catalog Volume, even though
    the file is there and readable over the Files API. The script is uploaded to
    the Workspace instead.
  - The job script must not touch spark.sparkContext: serverless refuses direct
    driver JVM access outright.
  - Nor use RDDs. Both of those live in processing/dol_spark.py, which is written
    to avoid them when DATABRICKS_RUNTIME_VERSION is set.

Usage:
    python scripts/dol_databricks_run.py
    python scripts/dol_databricks_run.py --skip-push     # re-run on what is there
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("dol_databricks")

VOLUME = "/Volumes/workspace/signal_dol/lake"
LOCAL_IN = "data/dol_parquet"
LOCAL_OUT = "data/dol_employer_summary"
JOB_NAME = "Signal - DOL employer summary"
CATALOG, SCHEMA, VOL = "workspace", "signal_dol", "lake"


class Databricks:
    def __init__(self) -> None:
        self.host = (os.getenv("DATABRICKS_HOST") or "").rstrip("/")
        self.token = os.getenv("DATABRICKS_TOKEN") or ""
        if not self.host or not self.token:
            raise SystemExit("DATABRICKS_HOST and DATABRICKS_TOKEN must be set")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {self.token}"

    def call(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{self.host}{path}", timeout=120, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content and r.headers.get("content-type", "").startswith(
            "application/json") else None

    # --- storage -----------------------------------------------------------
    def ensure_volume(self) -> None:
        """Create the schema and volume if this is a fresh workspace."""
        for path, body in (
            ("/api/2.1/unity-catalog/schemas",
             {"name": SCHEMA, "catalog_name": CATALOG,
              "comment": "Signal: DOL H-1B filings, pushed from the pipeline"}),
            ("/api/2.1/unity-catalog/volumes",
             {"catalog_name": CATALOG, "schema_name": SCHEMA, "name": VOL,
              "volume_type": "MANAGED"}),
        ):
            r = self.s.post(f"{self.host}{path}", json=body, timeout=120)
            if r.status_code < 400:
                log.info("  created %s", body["name"])
            elif "already exists" in r.text or r.status_code == 409:
                pass
            else:
                raise SystemExit(f"{path} -> {r.status_code}: {r.text[:240]}")

    def put_file(self, local: str, remote: str) -> None:
        with open(local, "rb") as fh:
            r = self.s.put(f"{self.host}/api/2.0/fs/files{remote}",
                           params={"overwrite": "true"}, data=fh, timeout=300)
        if r.status_code >= 400:
            raise SystemExit(f"upload {remote} -> {r.status_code}: {r.text[:240]}")

    def list_dir(self, remote: str) -> list[dict]:
        r = self.s.get(f"{self.host}/api/2.0/fs/directories{remote}", timeout=120)
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            raise SystemExit(f"list {remote} -> {r.status_code}: {r.text[:240]}")
        return (r.json() or {}).get("contents", []) or []

    def get_file(self, remote: str, local: str) -> None:
        r = self.s.get(f"{self.host}/api/2.0/fs/files{remote}", timeout=300)
        if r.status_code >= 400:
            raise SystemExit(f"download {remote} -> {r.status_code}: {r.text[:240]}")
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "wb") as fh:
            fh.write(r.content)

    # --- the job -----------------------------------------------------------
    def upload_script(self) -> str:
        """
        Into the Workspace, not the Volume.

        Serverless refuses to execute a python_file from a Volume - the first
        attempt failed with RESOURCE_NOT_FOUND while the file was demonstrably
        there and readable over the Files API.
        """
        import base64
        me = self.call("GET", "/api/2.0/preview/scim/v2/Me")
        user = me["emails"][0]["value"]
        folder = f"/Workspace/Users/{user}/signal"
        self.call("POST", "/api/2.0/workspace/mkdirs", json={"path": folder})
        with open("processing/dol_spark.py", "rb") as fh:
            content = base64.b64encode(fh.read()).decode()
        path = f"{folder}/dol_spark.py"
        self.call("POST", "/api/2.0/workspace/import",
                  json={"path": path, "format": "AUTO", "language": "PYTHON",
                        "overwrite": True, "content": content})
        log.info("  script -> %s", path)
        return path

    def upsert_job(self, script: str) -> int:
        settings = {
            "name": JOB_NAME,
            "max_concurrent_runs": 1,
            "tasks": [{
                "task_key": "aggregate",
                "environment_key": "default",
                "spark_python_task": {
                    "python_file": script,
                    "parameters": ["--in-dir", f"{VOLUME}/parquet",
                                   "--out-dir", f"{VOLUME}/employer_summary"],
                },
            }],
            # Serverless is the only compute Free Edition offers; naming a cluster
            # spec here simply fails to deploy.
            "environments": [{"environment_key": "default", "spec": {"client": "3"}}],
        }
        existing = self.call("GET", "/api/2.2/jobs/list", params={"limit": 25}) or {}
        for job in existing.get("jobs", []) or []:
            if job.get("settings", {}).get("name") == JOB_NAME:
                jid = job["job_id"]
                self.call("POST", "/api/2.2/jobs/reset",
                          json={"job_id": jid, "new_settings": settings})
                log.info("  reusing job %s", jid)
                return jid
        jid = self.call("POST", "/api/2.2/jobs/create", json=settings)["job_id"]
        log.info("  created job %s", jid)
        return jid

    def run_and_wait(self, job_id: int, timeout: int = 1800) -> None:
        run_id = self.call("POST", "/api/2.2/jobs/run-now",
                           json={"job_id": job_id})["run_id"]
        log.info("  run %s started", run_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.call("GET", "/api/2.2/jobs/runs/get", params={"run_id": run_id})
            status = r.get("status", {})
            state = status.get("state")
            if state in ("TERMINATED", "INTERNAL_ERROR", "SKIPPED"):
                details = status.get("termination_details") or {}
                if details.get("code") == "SUCCESS":
                    log.info("  run %s succeeded", run_id)
                    return
                task = r["tasks"][0]["run_id"]
                out = self.call("GET", "/api/2.2/jobs/runs/get-output",
                                params={"run_id": task}) or {}
                log.error("  run failed: %s", details.get("code"))
                if out.get("error"):
                    log.error("  %s", out["error"][:500])
                raise SystemExit(1)
            time.sleep(20)
        raise SystemExit(f"run {run_id} did not finish within {timeout}s")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the DOL aggregation on Databricks")
    ap.add_argument("--skip-push", action="store_true",
                    help="do not re-upload the Parquet; run on what is already there")
    args = ap.parse_args()

    dbx = Databricks()
    log.info("Workspace: %s", dbx.host)
    dbx.ensure_volume()

    if not args.skip_push:
        files = sorted(glob.glob(f"{LOCAL_IN}/**/*.parquet", recursive=True))
        if not files:
            raise SystemExit(f"No Parquet under {LOCAL_IN}/ - run ingestion/dol_ingest.py")
        log.info("Pushing %s file(s)", len(files))
        for f in files:
            rel = os.path.relpath(f, LOCAL_IN)
            dbx.put_file(f, f"{VOLUME}/parquet/{rel}")
            log.info("  %s", rel)

    log.info("Uploading the job script")
    script = dbx.upload_script()
    log.info("Running the aggregation")
    dbx.run_and_wait(dbx.upsert_job(script))

    log.info("Pulling the summary back")
    pulled = 0

    def walk(remote: str, local: str) -> None:
        nonlocal pulled
        for entry in dbx.list_dir(remote):
            name = entry["path"].rstrip("/").rsplit("/", 1)[-1]
            if entry.get("is_directory"):
                walk(entry["path"].rstrip("/"), os.path.join(local, name))
            elif not name.startswith("_"):
                dbx.get_file(entry["path"], os.path.join(local, name))
                pulled += 1
                log.info("  %s", os.path.join(os.path.relpath(local, LOCAL_OUT), name))

    import shutil
    shutil.rmtree(LOCAL_OUT, ignore_errors=True)
    walk(f"{VOLUME}/employer_summary", LOCAL_OUT)
    if not pulled:
        raise SystemExit("the job wrote no output")
    log.info("Pulled %s file(s). Next: python storage/load_dol.py", pulled)


if __name__ == "__main__":
    main()
