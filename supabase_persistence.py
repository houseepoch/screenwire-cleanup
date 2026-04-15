from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from runtime_logging import configure_logging

configure_logging("screenwire-runtime")

SCREENWIRE_PROJECTS_TABLE = "screenwire_projects"
SCREENWIRE_PROJECT_MEMBERSHIPS_TABLE = "screenwire_project_memberships"
SCREENWIRE_PROJECT_ASSETS_TABLE = "screenwire_project_assets"
SCREENWIRE_PROJECT_EXPORTS_TABLE = "screenwire_project_exports"
SCREENWIRE_PIPELINE_JOBS_TABLE = "screenwire_pipeline_jobs"
SCREENWIRE_GRAPH_SNAPSHOTS_TABLE = "screenwire_project_graph_snapshots"
SCREENWIRE_GRAPH_OPS_TABLE = "screenwire_project_graph_ops"
SCREENWIRE_CLAIM_PIPELINE_JOB_RPC = "claim_screenwire_pipeline_job"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _project_phase_status(manifest: dict[str, Any]) -> str:
    phases = manifest.get("phases") or {}
    if not isinstance(phases, dict):
        return "draft"
    for phase_number in range(0, 7):
        phase = phases.get(f"phase_{phase_number}") or {}
        status = str(phase.get("status") or "").strip().lower()
        if status != "complete":
            return status or "draft"
    return "complete"


def _normalize_rel_path(value: str | Path) -> str:
    return Path(value).as_posix().lstrip("./")


def guess_asset_kind(rel_path: str | Path) -> str:
    rel = _normalize_rel_path(rel_path)
    if rel.startswith("source_files/"):
        return "source_file"
    if rel.startswith("creative_output/"):
        return "creative_output"
    if rel.startswith("graph/"):
        return "graph_artifact"
    if rel.startswith("cast/"):
        return "cast_asset"
    if rel.startswith("locations/"):
        return "location_asset"
    if rel.startswith("props/"):
        return "prop_asset"
    if rel.startswith("storyboard/"):
        return "storyboard_asset"
    if rel.startswith("frames/composed/"):
        return "frame_image"
    if rel.startswith("frames/prompts/"):
        return "frame_prompt"
    if rel.startswith("video/clips/"):
        return "video_clip"
    if rel.startswith("video/prompts/"):
        return "video_prompt"
    if rel.startswith("video/export/"):
        return "video_export"
    if rel.startswith("reports/"):
        return "report_artifact"
    if rel.startswith("logs/"):
        return "log_artifact"
    return "project_file"


def should_persist_rel_path(rel_path: str | Path) -> bool:
    rel = _normalize_rel_path(rel_path)
    if not rel:
        return False
    if rel.startswith((".cache/", "cache/", "__pycache__/")):
        return False
    if rel.startswith("dispatch/manifest_queue/"):
        return False
    if rel.startswith("graph/assembly_queue/"):
        return False
    return True


def project_metadata_from_dir(project_dir: Path) -> dict[str, Any]:
    manifest = _read_json(project_dir / "project_manifest.json", {})
    onboarding = _read_json(project_dir / "source_files" / "onboarding_config.json", {})
    name = str(manifest.get("projectName") or onboarding.get("projectName") or project_dir.name)
    slug = str(manifest.get("slug") or project_dir.name)
    return {
        "id": project_dir.name,
        "slug": slug,
        "name": name,
        "manifest_project_id": str(manifest.get("projectId") or ""),
        "status": _project_phase_status(manifest),
        "storage_prefix": project_dir.name,
        "metadata": {
            "localProjectDir": str(project_dir),
            "frameBudget": onboarding.get("frameBudget"),
            "mediaStyle": onboarding.get("mediaStyle"),
            "creativeFreedom": onboarding.get("creativeFreedom"),
            "aspectRatio": onboarding.get("aspectRatio"),
            "pipeline": onboarding.get("pipeline"),
            "sourceFiles": onboarding.get("sourceFiles") or [],
        },
    }


@dataclass(slots=True)
class SupabaseSettings:
    url: str
    service_role_key: str
    asset_bucket: str
    export_bucket: str
    signed_url_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "SupabaseSettings | None":
        url = str(os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
        service_role_key = str(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not url or not service_role_key:
            return None
        return cls(
            url=url,
            service_role_key=service_role_key,
            asset_bucket=str(os.getenv("SUPABASE_PROJECT_ASSET_BUCKET") or "project-assets").strip() or "project-assets",
            export_bucket=str(os.getenv("SUPABASE_PROJECT_EXPORT_BUCKET") or "project-exports").strip() or "project-exports",
            signed_url_ttl_seconds=max(60, int(os.getenv("SUPABASE_SIGNED_URL_TTL_SECONDS") or "3600")),
        )


class SupabasePersistence:
    def __init__(self, settings: SupabaseSettings, *, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._http_client = http_client
        self._owns_client = http_client is None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.url and self.settings.service_role_key)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.settings.service_role_key,
            "Authorization": f"Bearer {self.settings.service_role_key}",
        }
        if extra:
            headers.update(extra)
        return headers

    @property
    def client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=120.0)
            self._owns_client = True
        return self._http_client

    async def aclose(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self.client.request(method, f"{self.settings.url}{path}", **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            details = response.text.strip()
            raise RuntimeError(f"Supabase request failed: {method} {path} -> {response.status_code} {details}") from exc
        return response

    async def upsert_rows(self, table: str, payload: dict[str, Any] | list[dict[str, Any]], *, on_conflict: str) -> list[dict[str, Any]]:
        headers = self._headers({"Prefer": "resolution=merge-duplicates,return=representation"})
        response = await self._request(
            "POST",
            f"/rest/v1/{table}",
            params={"on_conflict": on_conflict},
            headers=headers,
            json=payload,
        )
        data = response.json()
        return data if isinstance(data, list) else [data]

    async def select_rows(self, table: str, *, filters: dict[str, str], columns: str = "*") -> list[dict[str, Any]]:
        params = {"select": columns}
        params.update(filters)
        response = await self._request(
            "GET",
            f"/rest/v1/{table}",
            params=params,
            headers=self._headers(),
        )
        data = response.json()
        return data if isinstance(data, list) else []

    async def rpc(self, function_name: str, payload: dict[str, Any] | None = None) -> Any:
        response = await self._request(
            "POST",
            f"/rest/v1/rpc/{function_name}",
            headers=self._headers({"content-type": "application/json"}),
            json=payload or {},
        )
        return response.json()

    async def ensure_project(self, project_dir: Path, *, include_graph_snapshot: bool = False) -> dict[str, Any]:
        payload = project_metadata_from_dir(project_dir)
        rows = await self.upsert_rows(SCREENWIRE_PROJECTS_TABLE, payload, on_conflict="id")
        if include_graph_snapshot and (project_dir / "graph" / "narrative_graph.json").exists():
            await self.upsert_graph_snapshot(project_dir, reason="project_ensure")
        manifest = _read_json(project_dir / "project_manifest.json", {})
        export_rel = str(manifest.get("exportPath") or "").strip()
        if export_rel:
            export_path = project_dir / export_rel
            if export_path.exists():
                await self.ensure_asset_synced(project_dir, export_rel, local_path=export_path)
        return rows[0] if rows else payload

    async def fetch_project(self, project_id: str) -> dict[str, Any] | None:
        rows = await self.select_rows(
            SCREENWIRE_PROJECTS_TABLE,
            filters={"id": f"eq.{project_id}", "limit": "1"},
        )
        return rows[0] if rows else None

    async def upsert_graph_snapshot(self, project_dir: Path, *, reason: str) -> dict[str, Any] | None:
        graph_path = project_dir / "graph" / "narrative_graph.json"
        if not graph_path.exists():
            return None
        raw = graph_path.read_text(encoding="utf-8")
        try:
            graph_payload = json.loads(raw)
        except Exception:
            graph_payload = {}
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        payload = {
            "project_id": project_dir.name,
            "snapshot_kind": "current",
            "graph": graph_payload,
            "graph_checksum": digest,
            "metadata": {
                "reason": reason,
                "sourcePath": graph_path.relative_to(project_dir).as_posix(),
            },
            "updated_at": _utc_now(),
        }
        rows = await self.upsert_rows(SCREENWIRE_GRAPH_SNAPSHOTS_TABLE, payload, on_conflict="project_id,snapshot_kind")
        return rows[0] if rows else payload

    async def record_graph_op(
        self,
        *,
        project_id: str,
        operation: str,
        node_type: str,
        node_id: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/rest/v1/{SCREENWIRE_GRAPH_OPS_TABLE}",
            headers=self._headers({"Prefer": "return=representation"}),
            json={
                "project_id": project_id,
                "operation": operation,
                "node_type": node_type,
                "node_id": node_id,
                "actor": actor,
                "payload": payload or {},
                "created_at": _utc_now(),
            },
        )
        data = response.json()
        return data[0] if isinstance(data, list) and data else data

    async def get_asset_by_rel_path(self, project_id: str, rel_path: str) -> dict[str, Any] | None:
        rows = await self.select_rows(
            SCREENWIRE_PROJECT_ASSETS_TABLE,
            filters={
                "project_id": f"eq.{project_id}",
                "logical_path": f"eq.{_normalize_rel_path(rel_path)}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def list_project_assets(self, project_id: str) -> list[dict[str, Any]]:
        return await self.select_rows(
            SCREENWIRE_PROJECT_ASSETS_TABLE,
            filters={"project_id": f"eq.{project_id}", "order": "logical_path.asc"},
        )

    async def _upload_storage_object(self, bucket: str, object_path: str, content: bytes, *, content_type: str) -> None:
        encoded = quote(object_path, safe="/")
        await self._request(
            "POST",
            f"/storage/v1/object/{bucket}/{encoded}",
            headers=self._headers(
                {
                    "x-upsert": "true",
                    "content-type": content_type or "application/octet-stream",
                    "cache-control": "3600",
                }
            ),
            content=content,
        )

    async def create_signed_url(self, bucket: str, object_path: str, *, expires_in: int | None = None) -> str:
        encoded = quote(object_path, safe="/")
        response = await self._request(
            "POST",
            f"/storage/v1/object/sign/{bucket}/{encoded}",
            headers=self._headers({"content-type": "application/json"}),
            json={"expiresIn": expires_in or self.settings.signed_url_ttl_seconds},
        )
        data = response.json()
        signed = (
            data.get("signedURL")
            or data.get("signedUrl")
            or data.get("signed_url")
            or (data.get("data") or {}).get("signedURL")
            or (data.get("data") or {}).get("signedUrl")
        )
        if not signed:
            raise RuntimeError(f"Supabase sign URL response missing signed URL for {bucket}/{object_path}")
        if str(signed).startswith("http://") or str(signed).startswith("https://"):
            return str(signed)
        if str(signed).startswith("/storage/"):
            return f"{self.settings.url}{signed}"
        if str(signed).startswith("/object/"):
            return f"{self.settings.url}/storage/v1{signed}"
        return f"{self.settings.url}/storage/v1/{str(signed).lstrip('/')}"

    async def download_storage_object(self, bucket: str, object_path: str) -> bytes:
        encoded = quote(object_path, safe="/")
        response = await self._request(
            "GET",
            f"/storage/v1/object/authenticated/{bucket}/{encoded}",
            headers=self._headers(),
        )
        return response.content

    async def ensure_asset_synced(
        self,
        project_dir: Path,
        rel_path: str | Path,
        *,
        local_path: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_rel = _normalize_rel_path(rel_path)
        await self.ensure_project(project_dir)
        row = await self.get_asset_by_rel_path(project_dir.name, normalized_rel)
        resolved_local = local_path or (project_dir / normalized_rel)
        if not resolved_local.exists():
            if row is not None:
                return row
            raise FileNotFoundError(f"Local asset not found and no remote row exists for {normalized_rel}")

        content = resolved_local.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        byte_size = len(content)
        if row and str(row.get("checksum_sha256") or "") == checksum and int(row.get("byte_size") or 0) == byte_size:
            return row

        asset_kind = guess_asset_kind(normalized_rel)
        bucket = self.settings.export_bucket if asset_kind == "video_export" else self.settings.asset_bucket
        object_path = f"{project_dir.name}/{normalized_rel}"
        content_type = mimetypes.guess_type(str(resolved_local))[0] or "application/octet-stream"

        await self._upload_storage_object(bucket, object_path, content, content_type=content_type)

        asset_payload = {
            "project_id": project_dir.name,
            "logical_path": normalized_rel,
            "storage_bucket": bucket,
            "storage_object_path": object_path,
            "asset_kind": asset_kind,
            "file_name": resolved_local.name,
            "content_type": content_type,
            "byte_size": byte_size,
            "checksum_sha256": checksum,
            "metadata": metadata or {},
            "updated_at": _utc_now(),
        }
        rows = await self.upsert_rows(SCREENWIRE_PROJECT_ASSETS_TABLE, asset_payload, on_conflict="project_id,logical_path")
        asset_row = rows[0] if rows else asset_payload

        if asset_kind == "video_export":
            export_payload = {
                "project_id": project_dir.name,
                "logical_path": normalized_rel,
                "asset_id": asset_row.get("id"),
                "storage_bucket": bucket,
                "storage_object_path": object_path,
                "status": "available",
                "file_name": resolved_local.name,
                "export_format": resolved_local.suffix.lstrip(".").lower(),
                "metadata": metadata or {},
                "updated_at": _utc_now(),
            }
            await self.upsert_rows(SCREENWIRE_PROJECT_EXPORTS_TABLE, export_payload, on_conflict="project_id,logical_path")

        return asset_row

    async def get_signed_url_for_rel_path(self, project_dir: Path, rel_path: str | Path) -> str:
        normalized_rel = _normalize_rel_path(rel_path)
        local_path = project_dir / normalized_rel
        if local_path.exists() and should_persist_rel_path(normalized_rel):
            row = await self.ensure_asset_synced(project_dir, normalized_rel, local_path=local_path)
        else:
            row = await self.get_asset_by_rel_path(project_dir.name, normalized_rel)
            if row is None:
                raise FileNotFoundError(normalized_rel)
        return await self.create_signed_url(
            str(row["storage_bucket"]),
            str(row["storage_object_path"]),
        )

    async def mirror_remote_asset_to_cache(self, project_dir: Path, rel_path: str | Path) -> Path:
        normalized_rel = _normalize_rel_path(rel_path)
        local_path = project_dir / normalized_rel
        if local_path.exists():
            return local_path
        row = await self.get_asset_by_rel_path(project_dir.name, normalized_rel)
        if row is None:
            raise FileNotFoundError(normalized_rel)
        content = await self.download_storage_object(str(row["storage_bucket"]), str(row["storage_object_path"]))
        cache_path = project_dir / ".cache" / "supabase_mirror" / normalized_rel
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(content)
        return cache_path

    async def sync_project_tree(self, project_dir: Path) -> dict[str, int]:
        await self.ensure_project(project_dir, include_graph_snapshot=True)
        synced = 0
        skipped = 0
        for path in sorted(project_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(project_dir).as_posix()
            if not should_persist_rel_path(rel):
                skipped += 1
                continue
            if rel == "graph/narrative_graph.json":
                await self.upsert_graph_snapshot(project_dir, reason="tree_sync")
            await self.ensure_asset_synced(project_dir, rel, local_path=path)
            synced += 1
        return {"synced": synced, "skipped": skipped}

    async def hydrate_project_tree(self, project_id: str, projects_root: Path) -> Path:
        project_dir = projects_root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        assets = await self.list_project_assets(project_id)
        for asset in assets:
            rel = str(asset.get("logical_path") or "").strip()
            bucket = str(asset.get("storage_bucket") or "").strip()
            object_path = str(asset.get("storage_object_path") or "").strip()
            if not rel or not bucket or not object_path:
                continue
            target = project_dir / rel
            if target.exists() and target.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            content = await self.download_storage_object(bucket, object_path)
            target.write_bytes(content)
        return project_dir

    async def claim_pipeline_job(self, worker_name: str) -> dict[str, Any] | None:
        data = await self.rpc(SCREENWIRE_CLAIM_PIPELINE_JOB_RPC, {"target_worker": worker_name})
        if isinstance(data, list):
            return data[0] if data else None
        return data or None

    async def list_pipeline_jobs(self, project_id: str, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        if include_terminal:
            status_filter = "in.(queued,running,complete,error,cancel_requested)"
        else:
            status_filter = "in.(queued,running)"
        return await self.select_rows(
            SCREENWIRE_PIPELINE_JOBS_TABLE,
            filters={
                "project_id": f"eq.{project_id}",
                "status": status_filter,
                "order": "created_at.desc",
            },
        )

    async def get_pipeline_job(self, project_id: str, job_key: str) -> dict[str, Any] | None:
        rows = await self.select_rows(
            SCREENWIRE_PIPELINE_JOBS_TABLE,
            filters={
                "project_id": f"eq.{project_id}",
                "job_key": f"eq.{job_key}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def find_pipeline_job_by_key(self, job_key: str) -> dict[str, Any] | None:
        rows = await self.select_rows(
            SCREENWIRE_PIPELINE_JOBS_TABLE,
            filters={
                "job_key": f"eq.{job_key}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def update_pipeline_job(
        self,
        *,
        project_id: str,
        job_key: str,
        status: str,
        progress: int,
        message: str,
        active_phase: int | None = None,
        target_phase: int | None = None,
        cancel_requested: bool | None = None,
        payload: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        worker_name: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "project_id": project_id,
            "job_key": job_key,
            "status": status,
            "progress": progress,
            "message": message,
            "active_phase": active_phase,
            "target_phase": target_phase,
            "cancel_requested": cancel_requested if cancel_requested is not None else False,
            "payload": payload or {},
            "result": result or {},
            "worker_name": worker_name,
            "claimed_by": worker_name,
            "completed_at": _utc_now() if status in {"complete", "error"} else None,
            "updated_at": _utc_now(),
        }
        rows = await self.upsert_rows(SCREENWIRE_PIPELINE_JOBS_TABLE, row, on_conflict="project_id,job_key")
        return rows[0] if rows else row


_PERSISTENCE: SupabasePersistence | None = None


def get_supabase_persistence(http_client: httpx.AsyncClient | None = None) -> SupabasePersistence | None:
    global _PERSISTENCE
    settings = SupabaseSettings.from_env()
    if settings is None:
        return None
    if _PERSISTENCE is None:
        _PERSISTENCE = SupabasePersistence(settings, http_client=http_client)
        return _PERSISTENCE
    if http_client is not None and _PERSISTENCE.client is not http_client:
        _PERSISTENCE = SupabasePersistence(settings, http_client=http_client)
    return _PERSISTENCE


def schedule_graph_persistence(
    project_dir: Path,
    *,
    operation: str,
    node_type: str,
    node_id: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> None:
    persistence = get_supabase_persistence()
    if persistence is None:
        return

    async def _runner() -> None:
        try:
            await persistence.ensure_project(project_dir, include_graph_snapshot=True)
            await persistence.record_graph_op(
                project_id=project_dir.name,
                operation=operation,
                node_type=node_type,
                node_id=node_id,
                actor=actor,
                payload=payload,
            )
            await persistence.upsert_graph_snapshot(project_dir, reason=operation)
        except Exception as exc:
            logging.getLogger("SupabasePersistence").exception(
                "graph persistence failed",
                extra={
                    "event": "graph_persistence_failed",
                    "project_id": project_dir.name,
                    "fields": {
                        "node_type": node_type,
                        "node_id": node_id,
                        "operation": operation,
                        "actor": actor,
                    },
                },
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_runner())
        return
    loop.create_task(_runner())
