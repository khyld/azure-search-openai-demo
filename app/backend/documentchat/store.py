import asyncio
import json
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from azure.core.exceptions import (
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import ContainerClient

from .lifecycle import DocumentError, require_active, session_active, utc_now


class DocumentStore:
    def __init__(self, container: ContainerClient):
        self.container = container

    def record_path(self, owner: str, active_id: str) -> str:
        return f"documents/{owner}/{active_id}/state.json"

    def prefix(self, owner: str, active_id: str) -> str:
        return f"documents/{owner}/{active_id}/"

    async def read(self, path: str) -> dict:
        try:
            download = await self.container.download_blob(path)
            return json.loads(await download.readall())
        except ResourceNotFoundError:
            return {}

    async def write(self, path: str, value: dict, lease=None) -> None:
        await self.container.upload_blob(
            path,
            json.dumps(value, ensure_ascii=True).encode(),
            overwrite=True,
            lease=lease,
            content_settings=ContentSettings(content_type="application/json"),
        )

    @asynccontextmanager
    async def lock(self, path: str, create: bool = False):
        blob = self.container.get_blob_client(path)
        if create:
            try:
                await blob.upload_blob(b"{}", overwrite=False)
            except ResourceExistsError:
                pass
        try:
            lease = await blob.acquire_lease(lease_duration=60)
        except HttpResponseError as error:
            if error.status_code in (409, 412):
                raise DocumentError("Dokumentet behandles allerede. Prov igen om lidt.", 423) from None
            raise
        owner_task = asyncio.current_task()

        async def renew():
            try:
                while True:
                    await asyncio.sleep(20)
                    await lease.renew()
            except asyncio.CancelledError:
                raise
            except Exception:
                if owner_task:
                    owner_task.cancel()

        renewal = asyncio.create_task(renew())
        try:
            yield lease
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal
            with suppress(HttpResponseError):
                await lease.release()

    async def session(self, owner: str) -> dict:
        return await self.read(f"sessions/{owner}.json")

    async def active(self, owner: str, active_id: str, touch: bool = False) -> dict:
        path = f"sessions/{owner}.json"
        if touch:
            async with self.lock(path, create=True) as lease:
                session = await self.read(path)
                require_active(session, active_id)
                session["last_activity"] = utc_now().isoformat()
                await self.write(path, session, lease)
        else:
            require_active(await self.session(owner), active_id)
        record = await self.read(self.record_path(owner, active_id))
        if not record:
            raise DocumentError("Dokumentet findes ikke.", 404)
        return record

    async def replace(self, owner: str, active_id: str, filename: str, content: bytes, extension: str) -> dict:
        path = self.record_path(owner, active_id)
        record = {
            "owner": owner,
            "document_id": active_id,
            "filename": filename,
            "extension": extension,
            "status": "uploading",
            "created_at": utc_now().isoformat(),
        }
        async with self.lock(path, create=True) as lease:
            await self.write(path, record, lease)
            await self.container.upload_blob(self.prefix(owner, active_id) + "original" + extension, content)
            session_path = f"sessions/{owner}.json"
            async with self.lock(session_path, create=True) as session_lease:
                await self.write(
                    session_path,
                    {"document_id": active_id, "last_activity": utc_now().isoformat()},
                    session_lease,
                )
            record["status"] = "queued"
            await self.write(path, record, lease)
        return record

    async def end(self, owner: str, active_id: str) -> None:
        path = f"sessions/{owner}.json"
        async with self.lock(path, create=True) as lease:
            session = await self.read(path)
            if session.get("document_id") != active_id:
                raise DocumentError("Dokumentet er allerede erstattet.", 409)
            await self.write(path, {}, lease)

    async def logout(self, owner: str) -> None:
        path = f"sessions/{owner}.json"
        async with self.lock(path, create=True) as lease:
            await self.write(path, {}, lease)

    async def records(self):
        async for blob in self.container.list_blobs(name_starts_with="documents/"):
            if blob.name.endswith("/state.json"):
                record = await self.read(blob.name)
                if record:
                    yield record

    async def should_delete(self, record: dict) -> bool:
        session = await self.session(record["owner"])
        if record["status"] == "uploading":
            age = (utc_now() - datetime.fromisoformat(record["created_at"])).total_seconds()
            return age > 600 and not session_active(session, record["document_id"])
        return not session_active(session, record["document_id"])

    async def delete_files(self, owner: str, active_id: str) -> None:
        state_path = self.record_path(owner, active_id)
        async for blob in self.container.list_blobs(name_starts_with=self.prefix(owner, active_id)):
            if blob.name != state_path:
                await self.container.delete_blob(blob.name, delete_snapshots="include")
