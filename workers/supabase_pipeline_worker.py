#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
from pathlib import Path

from dotenv import load_dotenv

from runtime_logging import configure_logging, log_event

APP_DIR = Path(os.getenv("SCREENWIRE_APP_ROOT", Path(__file__).resolve().parents[1])).resolve()
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

PROJECTS_DIR = Path(os.getenv("SCREENWIRE_PROJECTS_ROOT", APP_DIR / "projects")).resolve()
POLL_INTERVAL_SECONDS = float(os.getenv("SCREENWIRE_WORKER_POLL_SECONDS", "2"))
WORKER_NAME = os.getenv("SCREENWIRE_WORKER_NAME") or f"{socket.gethostname()}:{os.getpid()}"
LOGGER = configure_logging("screenwire-worker")

from supabase_persistence import get_supabase_persistence


def _project_manifest_progress(project_dir: Path, target_phase: int) -> tuple[int, int, str]:
    manifest_path = project_dir / "project_manifest.json"
    if not manifest_path.exists():
        return target_phase, 5, f"Running pipeline phase {target_phase}"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return target_phase, 5, f"Running pipeline phase {target_phase}"

    phases = manifest.get("phases") or {}
    active_phase = target_phase
    completed = 0
    total = max(target_phase + 1, 1)
    for phase_number in range(target_phase + 1):
        phase = phases.get(f"phase_{phase_number}") or {}
        status = str(phase.get("status") or "").strip().lower()
        if status == "complete":
            completed += 1
            continue
        active_phase = phase_number
        break
    progress = min(95, max(5, round((completed / total) * 100)))
    return active_phase, progress, f"Running pipeline phase {active_phase}"


def _build_pipeline_command(project_id: str, target_phase: int) -> list[str]:
    cmd = [sys.executable, str(APP_DIR / "run_pipeline.py"), "--project", project_id]
    if target_phase >= 4:
        cmd.extend(["--phase", str(target_phase)])
    else:
        cmd.extend(["--resume", "--through-phase", str(target_phase)])
    cmd.append("--live")
    return cmd


async def _current_job_row(persistence, project_id: str, job_key: str) -> dict | None:
    return await persistence.get_pipeline_job(project_id, job_key)


async def _stream_subprocess_output(
    stream: asyncio.StreamReader | None,
    *,
    level: int,
    event: str,
    project_id: str,
    job_key: str,
) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode(errors="ignore").rstrip()
        if not text:
            continue
        log_event(
            LOGGER,
            level,
            event,
            project_id=project_id,
            job_key=job_key,
            line=text,
        )


async def run_claimed_job(job: dict) -> None:
    persistence = get_supabase_persistence()
    if persistence is None:
        raise RuntimeError("Supabase persistence is required for the queue worker.")

    project_id = str(job.get("project_id") or "").strip()
    job_key = str(job.get("job_key") or job.get("id") or "").strip()
    target_phase = int(job.get("target_phase") or 0)
    if not project_id or not job_key:
        return

    log_event(LOGGER, logging.INFO, "worker_job_start", project_id=project_id, job_key=job_key, target_phase=target_phase)
    project_dir = await persistence.hydrate_project_tree(project_id, PROJECTS_DIR)
    log_event(LOGGER, logging.INFO, "worker_project_hydrated", project_id=project_id, job_key=job_key, project_dir=str(project_dir))
    env = dict(os.environ)
    env["SCREENWIRE_APP_ROOT"] = str(APP_DIR)
    env["SCREENWIRE_PROJECTS_ROOT"] = str(PROJECTS_DIR)

    command = _build_pipeline_command(project_id, target_phase)
    log_event(LOGGER, logging.INFO, "worker_pipeline_spawn", project_id=project_id, job_key=job_key, command=command)
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(APP_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_task = asyncio.create_task(
        _stream_subprocess_output(
            proc.stdout,
            level=logging.INFO,
            event="worker_pipeline_stdout",
            project_id=project_id,
            job_key=job_key,
        )
    )
    stderr_task = asyncio.create_task(
        _stream_subprocess_output(
            proc.stderr,
            level=logging.WARNING,
            event="worker_pipeline_stderr",
            project_id=project_id,
            job_key=job_key,
        )
    )
    wait_task = asyncio.create_task(proc.wait())
    try:
        while not wait_task.done():
            current = await _current_job_row(persistence, project_id, job_key)
            if current and bool(current.get("cancel_requested")):
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                await persistence.update_pipeline_job(
                    project_id=project_id,
                    job_key=job_key,
                    status="error",
                    progress=100,
                    message="Stopped by user",
                    active_phase=target_phase,
                    target_phase=target_phase,
                    cancel_requested=True,
                    worker_name=WORKER_NAME,
                )
                log_event(LOGGER, logging.WARNING, "worker_job_cancelled", project_id=project_id, job_key=job_key)
                return

            active_phase, progress, message = _project_manifest_progress(project_dir, target_phase)
            await persistence.update_pipeline_job(
                project_id=project_id,
                job_key=job_key,
                status="running",
                progress=progress,
                message=message,
                active_phase=active_phase,
                target_phase=target_phase,
                payload={
                    "phaseNumbers": [target_phase],
                    "cancelRequested": False,
                },
                worker_name=WORKER_NAME,
            )
            await asyncio.sleep(2)

        await wait_task
        await asyncio.gather(stdout_task, stderr_task)

        if proc.returncode == 0:
            await persistence.sync_project_tree(project_dir)
            await persistence.upsert_graph_snapshot(project_dir, reason="worker_complete")
            await persistence.update_pipeline_job(
                project_id=project_id,
                job_key=job_key,
                status="complete",
                progress=100,
                message="Pipeline job completed",
                active_phase=target_phase,
                target_phase=target_phase,
                worker_name=WORKER_NAME,
            )
            log_event(LOGGER, logging.INFO, "worker_job_complete", project_id=project_id, job_key=job_key, return_code=proc.returncode)
        else:
            await persistence.sync_project_tree(project_dir)
            await persistence.update_pipeline_job(
                project_id=project_id,
                job_key=job_key,
                status="error",
                progress=100,
                message=f"Pipeline job failed with exit {proc.returncode}",
                active_phase=target_phase,
                target_phase=target_phase,
                worker_name=WORKER_NAME,
            )
            log_event(LOGGER, logging.ERROR, "worker_job_failed", project_id=project_id, job_key=job_key, return_code=proc.returncode)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        stdout_task.cancel()
        stderr_task.cancel()


async def main() -> None:
    load_dotenv(APP_DIR / ".env")
    persistence = get_supabase_persistence()
    if persistence is None:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")

    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    log_event(
        LOGGER,
        logging.INFO,
        "worker_start",
        worker_name=WORKER_NAME,
        projects_root=str(PROJECTS_DIR),
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
    )

    while True:
        job = await persistence.claim_pipeline_job(WORKER_NAME)
        if not job:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        log_event(
            LOGGER,
            logging.INFO,
            "worker_job_claimed",
            worker_name=WORKER_NAME,
            job_key=str(job.get("job_key") or ""),
            project_id=str(job.get("project_id") or ""),
            target_phase=int(job.get("target_phase") or 0),
        )
        try:
            await run_claimed_job(job)
        except Exception as exc:
            project_id = str(job.get("project_id") or "")
            job_key = str(job.get("job_key") or job.get("id") or "")
            if project_id and job_key:
                await persistence.update_pipeline_job(
                    project_id=project_id,
                    job_key=job_key,
                    status="error",
                    progress=100,
                    message=f"Worker error: {exc}",
                    active_phase=int(job.get("target_phase") or 0),
                    target_phase=int(job.get("target_phase") or 0),
                    worker_name=WORKER_NAME,
                )
            LOGGER.exception(
                "Queued worker job failed",
                extra={
                    "event": "worker_job_exception",
                    "project_id": project_id or None,
                    "fields": {
                        "job_key": job_key,
                        "worker_name": WORKER_NAME,
                    },
                },
            )


if __name__ == "__main__":
    asyncio.run(main())
