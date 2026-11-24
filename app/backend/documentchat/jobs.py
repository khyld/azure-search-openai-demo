import asyncio
import logging
import uuid
from contextlib import suppress
from datetime import datetime

from azure.storage.blob import ContentSettings

from .artifacts import SummaryGenerator, export_docx
from .budget import ArtifactBudget
from .lifecycle import DocumentError, utc_now

logger = logging.getLogger("documentchat")
KINDS = ("summary", "translation")
TERMINAL = ("ready", "not_needed", "failed", "cancelled", "submission_unknown")


class ArtifactJobs:
    def __init__(self, service, translator=None):
        self.service = service
        self.store = service.store
        self.translator = translator
        self.budget = ArtifactBudget(self.store)
        self.paid_lease = None

    def path(self, owner: str, active_id: str, kind: str) -> str:
        if kind not in KINDS:
            raise DocumentError("Ukendt dokumentfunktion.")
        return self.store.prefix(owner, active_id) + f"jobs/{kind}.json"

    def execution_path(self, owner: str, active_id: str) -> str:
        return f"coordination/artifacts/{owner}/{active_id}.json"

    def public(self, job: dict) -> dict:
        return {key: job[key] for key in ("job_id", "kind", "status", "created_at", "updated_at", "error") if key in job}

    async def list(self, owner: str, active_id: str) -> list[dict]:
        await self.store.active(owner, active_id)
        jobs = [await self.store.read(self.path(owner, active_id, kind)) for kind in KINDS]
        await self.store.active(owner, active_id)
        return [self.public(job) for job in jobs if job.get("job_id")]

    async def request(self, owner: str, active_id: str, kind: str, language: str = "auto", retry: bool = False) -> dict:
        path = self.path(owner, active_id, kind)
        if not self.service.enable_artifacts or (kind == "translation" and self.translator is None):
            raise DocumentError("Dokumentfunktionen er ikke aktiveret.", 409)
        if language not in ("auto", "da", "en", "de"):
            raise DocumentError("Vaelg dansk, engelsk, tysk eller automatisk sprogvalg.")
        await self.store.active(owner, active_id, touch=True)
        async with self.store.lock(path, create=True) as lease:
            await self.store.active(owner, active_id)
            previous = await self.store.read(path)
            if previous.get("job_id"):
                if not retry or previous.get("status") not in ("failed", "cancelled"):
                    return self.public(previous)
            now = utc_now().isoformat()
            job = {"job_id": str(uuid.uuid4()), "kind": kind, "status": "queued", "source_language": language,
                   "created_at": now, "updated_at": now}
            await self.store.write(path, job, lease)
        return self.public(job)

    async def find(self, owner: str, active_id: str, job_id: str) -> dict:
        await self.store.active(owner, active_id)
        for kind in KINDS:
            job = await self.store.read(self.path(owner, active_id, kind))
            if job.get("job_id") == job_id:
                return job
        raise DocumentError("Opgaven findes ikke.", 404)

    async def cancel(self, owner: str, active_id: str, job_id: str) -> dict:
        job = await self.find(owner, active_id, job_id)
        path = self.path(owner, active_id, job["kind"])
        async with self.store.lock(path) as lease:
            await self.store.active(owner, active_id, touch=True)
            job = await self.store.read(path)
            if job.get("job_id") != job_id:
                raise DocumentError("Opgaven er allerede erstattet.", 409)
            if job["status"] not in TERMINAL or (job["kind"] == "translation" and job["status"] == "submission_unknown"):
                job.update(status="cancelling" if job["kind"] == "translation" and job["status"] in ("running", "submission_unknown") else "cancelled", updated_at=utc_now().isoformat())
                await self.store.write(path, job, lease)
        return self.public(job)

    async def checkpoint(self, owner: str, active_id: str, job: dict) -> None:
        await self.store.active(owner, active_id)
        current = await self.store.read(self.path(owner, active_id, job["kind"]))
        if current.get("job_id") != job["job_id"] or current.get("status") != "running":
            raise DocumentError("Opgaven er afbrudt.", 409)

    async def result(self, owner: str, active_id: str, job_id: str, download: bool = False):
        job = await self.find(owner, active_id, job_id)
        if job.get("status") != "ready":
            raise DocumentError("Resultatet er ikke klar.", 409)
        prefix = self.store.prefix(owner, active_id) + f"artifacts/{job_id}/"
        if download:
            blob = await self.store.container.download_blob(prefix + "result.docx")
            result = await blob.readall()
        elif job["kind"] == "summary":
            result = await self.store.read(prefix + "summary.json")
        else:
            raise DocumentError("Oversættelsen har ingen forhåndsvisning.", 404)
        await self.store.active(owner, active_id, touch=True)
        return result

    async def process(self, owner: str, active_id: str, kind: str) -> None:
        if kind == "translation":
            await self.start_translation(owner, active_id)
            return
        path = self.path(owner, active_id, kind)
        async with self.store.lock(path) as lease:
            job = await self.store.read(path)
            if job.get("status") not in ("queued", "running"):
                return
            await self.store.active(owner, active_id)
            if job["status"] == "running":
                job.update(status="submission_unknown", error="En tidligere k\u00f8rsel blev afbrudt. Ingen automatisk genindsendelse.")
                await self.store.write(path, job, lease)
                return
            record = await self.store.active(owner, active_id)
            if record["status"] != "ready":
                return
            job.update(status="running", updated_at=utc_now().isoformat())
            await self.store.write(path, job, lease)
        try:
            extraction = await self.store.read(self.store.prefix(owner, active_id) + "extraction.json")
            if extraction.get("coverage", {}).get("status") != "adequate":
                raise DocumentError("Hele dokumentet kunne ikke laeses sikkert. Upload en bedre dokumentfil.")
            async def checkpoint():
                await self.checkpoint(owner, active_id, job)

            await checkpoint()
            if kind == "summary":
                async def before_call():
                    await self.budget.reserve_model(self.service.chat_model, "summary")

                result = await asyncio.wait_for(SummaryGenerator(self.service.openai, self.service.chat_model).generate(extraction, checkpoint, before_call), timeout=1800)
                content = await asyncio.to_thread(export_docx, "Resum\u00e9", result["content"], result["sources"], record["extension"] == ".pdf")
            else:
                if self.translator is None:
                    raise DocumentError("Oversættelse er ikke aktiveret.")
                content = await self.translator.translate(extraction, job, checkpoint)
                result = None
            await checkpoint()
            prefix = self.store.prefix(owner, active_id) + f"artifacts/{job['job_id']}/"
            await self.store.container.upload_blob(prefix + "result.docx", content, overwrite=True,
                content_settings=ContentSettings(content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
            if result is not None:
                await self.store.write(prefix + "summary.json", result)
            async with self.store.lock(path) as lease:
                await checkpoint()
                job.update(status="ready", updated_at=utc_now().isoformat())
                await self.store.write(path, job, lease)
        except DocumentError as error:
            async with self.store.lock(path) as lease:
                current = await self.store.read(path)
                if current.get("job_id") == job["job_id"] and current.get("status") == "running":
                    job.update(status="cancelled" if error.status == 409 else "failed", error=str(error), updated_at=utc_now().isoformat())
                    await self.store.write(path, job, lease)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Artifact job interrupted; no automatic provider resubmission")
            async with self.store.lock(path) as lease:
                current = await self.store.read(path)
                if current.get("job_id") == job["job_id"] and current.get("status") == "running":
                    job.update(status="submission_unknown", error="Behandlingen blev afbrudt. Ingen automatisk genindsendelse.", updated_at=utc_now().isoformat())
                    await self.store.write(path, job, lease)

    async def start_translation(self, owner: str, active_id: str) -> None:
        if self.translator is None:
            return
        path = self.path(owner, active_id, "translation")
        async with self.store.lock(path) as lease:
            job = await self.store.read(path)
            if job.get("status") == "running" and not await self.store.read(self.translator.operation_path(owner, active_id, job["job_id"])):
                job.update(status="submission_unknown", error="En tidligere koersel blev afbrudt. Ingen automatisk genindsendelse.")
                await self.store.write(path, job, lease)
                return
            if job.get("status") != "queued":
                return
            record = await self.store.active(owner, active_id)
            if record["status"] != "ready":
                return
            job.update(status="running", updated_at=utc_now().isoformat())
            await self.store.write(path, job, lease)
        try:
            extraction = await self.store.read(self.store.prefix(owner, active_id) + "extraction.json")
            if extraction.get("coverage", {}).get("status") != "adequate":
                raise DocumentError("Hele dokumentet kunne ikke laeses sikkert. Upload en bedre dokumentfil.")

            async def checkpoint():
                await self.checkpoint(owner, active_id, job)

            await checkpoint()
            if job["source_language"] == "auto":
                await self.budget.reserve_model(self.service.chat_model, "language")
            language = await self.translator.language(extraction, job["source_language"], checkpoint)
            if language == "da":
                async with self.store.lock(path) as lease:
                    await checkpoint()
                    job.update(status="not_needed", updated_at=utc_now().isoformat())
                    await self.store.write(path, job, lease)
                return
            job["source_language"] = language
            await checkpoint()
            reservation_id = await self.budget.reserve_translation(extraction)
            await checkpoint()
            operation = await self.translator.stage(owner, active_id, job, extraction)
            operation["budget_reservation_id"] = reservation_id
            await checkpoint()
            operation_path = self.translator.operation_path(owner, active_id, job["job_id"])
            await self.store.write("coordination/paid-artifact.json", {"operation_path": operation_path}, self.paid_lease)
            operation = await self.translator.submit(operation)
            if operation["status"] == "submission_unknown":
                async with self.store.lock(path) as lease:
                    current = await self.store.read(path)
                    if current.get("job_id") == job["job_id"] and current["status"] == "running":
                        current.update(status="submission_unknown", error="Translatorens modtagelse er uafklaret. Ingen automatisk genindsendelse.")
                        await self.store.write(path, current, lease)
        except DocumentError as error:
            async with self.store.lock(path) as lease:
                current = await self.store.read(path)
                if current.get("job_id") == job["job_id"] and current.get("status") == "running":
                    current.update(status="cancelled" if error.status == 409 else "failed", error=str(error))
                    await self.store.write(path, current, lease)
        except Exception:
            logger.warning("Translation interrupted; durable operation retained for reconciliation")
            async with self.store.lock(path) as lease:
                current = await self.store.read(path)
                if current.get("job_id") == job["job_id"] and current.get("status") == "running":
                    current.update(status="submission_unknown", error="Behandlingen blev afbrudt. Ingen automatisk genindsendelse.")
                    await self.store.write(path, current, lease)

    async def maintain_translations(self) -> None:
        if self.translator is None:
            return
        async for blob in self.store.container.list_blobs(name_starts_with="translation-operations/"):
            operation = await self.store.read(blob.name)
            if not operation:
                continue
            owner, active_id = operation["owner"], operation["document_id"]
            try:
                async with self.store.lock(self.execution_path(owner, active_id), create=True):
                    operation = await self.store.read(blob.name)
                    if not operation:
                        continue
                    path = self.path(owner, active_id, "translation")
                    job = await self.store.read(path)
                    active = job.get("job_id") == operation["job_id"] and job.get("status") in ("running", "submission_unknown")
                    try:
                        await self.store.active(owner, active_id)
                    except DocumentError as error:
                        if error.status not in (404, 409):
                            raise
                        active = False
                    expired = (utc_now() - datetime.fromisoformat(operation["created_at"])).total_seconds() > 1800
                    if operation["status"] == "prepared":
                        operation.update(status="terminal", provider_status="Abandoned")
                    elif operation["status"] == "submission_unknown":
                        operation = await self.translator.reconcile(operation)
                    if operation.get("provider_id") and operation["status"] != "terminal":
                        operation = await self.translator.poll(operation, cancel=not active or expired)
                    if operation["status"] != "terminal":
                        if not active or operation["status"] == "submission_unknown":
                            await self.translator.delete_staging(operation)
                        continue
                    if active and not expired:
                        try:
                            content = await self.translator.output(operation)
                            await self.store.active(owner, active_id)
                            prefix = self.store.prefix(owner, active_id) + f"artifacts/{operation['job_id']}/"
                            await self.store.container.upload_blob(prefix + "result.docx", content, overwrite=True,
                                content_settings=ContentSettings(content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
                            status, error_message = "ready", None
                        except DocumentError as error:
                            status, error_message = "failed", str(error)
                    else:
                        status, error_message = "cancelled", None
                    if job.get("job_id") == operation["job_id"]:
                        async with self.store.lock(path) as lease:
                            current = await self.store.read(path)
                            if current.get("job_id") != operation["job_id"] or current.get("status") not in ("running", "submission_unknown", "cancelling"):
                                current = None
                            if current is not None:
                                if current.get("status") in ("cancelling", "cancelled"):
                                    status = "cancelled"
                                try:
                                    await self.store.active(owner, active_id)
                                except DocumentError as error:
                                    if error.status not in (404, 409):
                                        raise
                                    status = "cancelled"
                                current.update(status=status, updated_at=utc_now().isoformat())
                                if error_message:
                                    current["error"] = error_message
                                else:
                                    current.pop("error", None)
                                await self.store.write(path, current, lease)
                    await self.translator.delete_staging(operation)
                    async with self.store.lock("coordination/paid-artifact.json", create=True) as lease:
                        reservation = await self.store.read("coordination/paid-artifact.json")
                        if reservation.get("operation_path") == blob.name:
                            await self.store.write("coordination/paid-artifact.json", {}, lease)
                    await self.store.container.delete_blob(blob.name)
            except DocumentError as error:
                if error.status not in (409, 423):
                    raise

    async def worker(self, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            try:
                await self.maintain_translations()
                async for record in self.store.records():
                    if stopped.is_set():
                        return
                    if record["status"] != "ready" or await self.store.should_delete(record):
                        continue
                    if not self.service.enable_artifacts:
                        continue
                    owner, active_id = record["owner"], record["document_id"]
                    for kind in KINDS:
                        job = await self.store.read(self.path(owner, active_id, kind))
                        if job.get("status") not in ("queued", "running"):
                            continue
                        try:
                            async with self.store.lock("coordination/paid-artifact.json", create=True) as paid_lease:
                                if (await self.store.read("coordination/paid-artifact.json")).get("operation_path"):
                                    continue
                                self.paid_lease = paid_lease
                                async with self.store.lock(self.execution_path(owner, active_id), create=True):
                                    await self.process(owner, active_id, kind)
                        except DocumentError as error:
                            if error.status not in (409, 423):
                                raise
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Artifact maintenance unavailable; retry scheduled")
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopped.wait(), timeout=3)