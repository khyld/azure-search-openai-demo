import asyncio
import io
import json
import os
import uuid
from contextlib import suppress
from pathlib import Path, PurePath

import jwt
from azure.identity.aio import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.search.documents.aio import SearchClient
from azure.storage.blob.aio import ContainerClient
from quart import Quart, g, jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from core.authentication import AuthenticationHelper, AuthError
from prepdocslib.pdfparser import DocumentAnalysisParser
from prepdocslib.servicesetup import OpenAIHost, setup_openai_client

from .extraction import ArtifactDocumentParser
from .jobs import ArtifactJobs
from .lifecycle import (
    MAX_BYTES,
    DocumentError,
    document_id,
    owner_key,
    session_active,
    validate_upload,
)
from .service import DocumentService
from .store import DocumentStore
from .translation import DocumentTranslator


def create_app(service: DocumentService | None = None, authenticate=None) -> Quart:
    static_folder = str(Path(__file__).resolve().parent.parent / "static")
    app = Quart(__name__, static_folder=static_folder, static_url_path="/assets-unused")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES + 1024 * 1024
    app.config["BODY_TIMEOUT"] = 120
    app.config["RESPONSE_TIMEOUT"] = 180
    app.config["DOCUMENT_SERVICE"] = service
    if service is not None and isinstance(service, DocumentService) and service.enable_artifacts:
        service.artifact_jobs = ArtifactJobs(service)
    build_file = Path(static_folder).parent / "documentchat-build.json"
    app.config["DOCUMENT_BUILD_ID"] = json.loads(build_file.read_text())["id"] if build_file.exists() else "local"

    @app.before_serving
    async def startup():
        if service is not None:
            return
        container_name = os.getenv("DOCUMENTCHAT_STORAGE_CONTAINER", "ab-documentchat")
        index_name = os.getenv("DOCUMENTCHAT_SEARCH_INDEX", "ab-documentchat")
        if not container_name.startswith("ab-documentchat") or not index_name.startswith("ab-documentchat"):
            raise RuntimeError("Document chat requires isolated ab-documentchat resources")
        credential = ManagedIdentityCredential() if os.getenv("WEBSITE_HOSTNAME") else AzureDeveloperCliCredential()
        container = ContainerClient(
            f"https://{os.environ['AZURE_STORAGE_ACCOUNT']}.blob.core.windows.net", container_name, credential
        )
        search = SearchClient(
            endpoint=f"https://{os.environ['AZURE_SEARCH_SERVICE']}.search.windows.net",
            index_name=index_name,
            credential=credential,
        )
        openai, _ = setup_openai_client(
            OpenAIHost.AZURE, credential, azure_openai_service=os.environ["AZURE_OPENAI_SERVICE"]
        )
        parser = DocumentAnalysisParser(
            f"https://{os.environ['AZURE_DOCUMENTINTELLIGENCE_SERVICE']}.cognitiveservices.azure.com/", credential
        )
        runtime = DocumentService(
            DocumentStore(container),
            search,
            openai,
            parser,
            os.environ["AZURE_OPENAI_CHATGPT_DEPLOYMENT"],
            os.environ["AZURE_OPENAI_EMB_DEPLOYMENT"],
            enable_artifacts=os.getenv("DOCUMENTCHAT_ENABLE_ARTIFACTS", "false").lower() == "true",
        )
        translator = None
        translator_name = os.getenv("DOCUMENTCHAT_TRANSLATOR_SERVICE")
        if translator_name:
            account_url = f"https://{os.environ['AZURE_STORAGE_ACCOUNT']}.blob.core.windows.net"
            translator = DocumentTranslator(translator_name, credential,
                ContainerClient(account_url, "ab-documentchat-translation-source", credential),
                ContainerClient(account_url, "ab-documentchat-translation-target", credential),
                runtime.store, openai, runtime.chat_model)
        runtime.artifact_jobs = ArtifactJobs(runtime, translator)
        if runtime.enable_artifacts:
            runtime.parser = ArtifactDocumentParser(parser.endpoint, credential)
        app.config["DOCUMENT_SERVICE"] = runtime
        app.config["DOCUMENT_CREDENTIAL"] = credential
        app.config["DOCUMENT_AUTH"] = AuthenticationHelper(
            None,
            False,
            os.environ["AZURE_SERVER_APP_ID"],
            None,
            os.environ["AZURE_CLIENT_APP_ID"],
            os.environ["AZURE_AUTH_TENANT_ID"],
        )
        stopped = asyncio.Event()
        app.config["DOCUMENT_STOPPED"] = stopped
        app.config["DOCUMENT_WORKER"] = asyncio.create_task(runtime.worker(stopped))
        if runtime.artifact_jobs is not None:
            app.config["ARTIFACT_WORKER"] = asyncio.create_task(runtime.artifact_jobs.worker(stopped))

    @app.after_serving
    async def shutdown():
        if "DOCUMENT_WORKER" not in app.config:
            return
        app.config["DOCUMENT_STOPPED"].set()
        app.config["DOCUMENT_WORKER"].cancel()
        with suppress(asyncio.CancelledError):
            await app.config["DOCUMENT_WORKER"]
        if "ARTIFACT_WORKER" in app.config:
            app.config["ARTIFACT_WORKER"].cancel()
            with suppress(asyncio.CancelledError):
                await app.config["ARTIFACT_WORKER"]
        runtime = app.config["DOCUMENT_SERVICE"]
        if runtime.artifact_jobs is not None and runtime.artifact_jobs.translator is not None:
            await runtime.artifact_jobs.translator.close()
        await runtime.search.close()
        await runtime.openai.close()
        await runtime.store.container.close()
        await app.config["DOCUMENT_CREDENTIAL"].close()

    async def current_owner() -> str:
        if authenticate is not None:
            return await authenticate()
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not token and os.getenv("WEBSITE_HOSTNAME"):
            token = request.headers.get("X-MS-TOKEN-AAD-ACCESS-TOKEN", "")
        if not token:
            raise DocumentError("Log ind for at bruge dokumentchatten.", 401)
        try:
            await app.config["DOCUMENT_AUTH"].validate_access_token(token)
            claims = jwt.decode(token, options={"verify_signature": False})
            if claims.get("tid") != os.environ["AZURE_AUTH_TENANT_ID"]:
                raise DocumentError("Forkert tenant.", 403)
            if "access_as_user" not in claims.get("scp", "").split():
                raise DocumentError("Brugeradgang til API'et er pakraevet.", 403)
            return owner_key(claims.get("tid", ""), claims.get("oid", ""))
        except (AuthError, jwt.PyJWTError):
            raise DocumentError("Login er udlobet. Log ind igen.", 401) from None

    @app.before_request
    async def protect_api():
        if request.path.startswith("/api/"):
            if request.method not in ("GET", "HEAD"):
                expected_origin = f"https://{os.environ['WEBSITE_HOSTNAME']}" if os.getenv("WEBSITE_HOSTNAME") else request.host_url.rstrip("/")
                if request.headers.get("Origin") != expected_origin:
                    raise DocumentError("Foresporgslen skal komme fra denne webside.", 403)
            g.document_owner = await current_owner()

    async def json_body() -> dict:
        body = await request.get_json(silent=True)
        if not isinstance(body, dict):
            raise DocumentError("Foresporgslen skal indeholde et JSON-objekt.")
        return body

    def artifact_jobs() -> ArtifactJobs:
        runtime = app.config["DOCUMENT_SERVICE"]
        if not isinstance(runtime, DocumentService) or not runtime.enable_artifacts or runtime.artifact_jobs is None:
            raise DocumentError("Dokumentfunktionerne er ikke aktiveret.", 404)
        return runtime.artifact_jobs

    def capabilities() -> dict:
        runtime = app.config["DOCUMENT_SERVICE"]
        enabled = isinstance(runtime, DocumentService) and runtime.enable_artifacts and runtime.artifact_jobs is not None
        return {"summary": enabled, "translation": bool(enabled and runtime.artifact_jobs.translator is not None)}

    @app.after_request
    async def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-DocumentChat-Build"] = app.config["DOCUMENT_BUILD_ID"]
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.errorhandler(DocumentError)
    async def document_error(error):
        return jsonify({"error": str(error)}), error.status

    @app.errorhandler(RequestEntityTooLarge)
    async def too_large(error):
        return jsonify({"error": "Dokumentet ma hojst fylde 20 MB."}), 413

    @app.errorhandler(Exception)
    async def unexpected(error):
        app.logger.error("Document request failed (%s)", type(error).__name__)
        return jsonify({"error": "Tjenesten kunne ikke behandle foresporgslen. Prov igen om lidt."}), 503

    @app.get("/")
    async def index():
        return await send_file(Path(static_folder) / "documentchat.html")

    @app.get("/healthz")
    async def health():
        runtime = app.config["DOCUMENT_SERVICE"]
        await runtime.store.container.get_container_properties()
        results = await runtime.search.search(search_text="*", top=0, include_total_count=True)
        await results.get_count()
        if request.args.get("artifacts") == "1":
            jobs = runtime.artifact_jobs
            if jobs is None or jobs.translator is None:
                raise DocumentError("Translator er ikke konfigureret.", 503)
            await jobs.translator.source.get_container_properties()
            await jobs.translator.target.get_container_properties()
            response = await jobs.translator.request("GET", "?%24maxpagesize=1")
            response.raise_for_status()
            ledger = await runtime.store.read(jobs.budget.path)
            return jsonify({"status": "ok", "artifacts": {
                "enabled": bool(runtime.enable_artifacts), "translator_access": True,
                "staging_access": True, "budget_approved": jobs.budget.valid(ledger),
            }})
        return jsonify({"status": "ok"})

    @app.get("/assets/<path:filename>")
    async def assets(filename):
        return await app.send_static_file("assets/" + filename)

    @app.get("/documentchat/<path:filename>")
    async def public_asset(filename):
        return await app.send_static_file("documentchat/" + filename)

    @app.get("/api/session")
    async def session():
        runtime = app.config["DOCUMENT_SERVICE"]
        state = await runtime.store.session(g.document_owner)
        active_id = state.get("document_id")
        if not active_id or not session_active(state, active_id):
            return jsonify({"document": None, "expired": bool(active_id), "capabilities": capabilities()})
        record = await runtime.store.active(g.document_owner, active_id)
        result = {"document": record, "last_activity": state["last_activity"], "capabilities": capabilities()}
        if capabilities()["summary"]:
            result["jobs"] = await artifact_jobs().list(g.document_owner, active_id)
        if request.args.get("history") == "1":
            result["messages"] = await runtime.history(g.document_owner, active_id)
        return jsonify(result)

    @app.post("/api/upload")
    async def upload():
        files = await request.files
        form = await request.form
        requested = [kind for kind in ("summary", "translation") if form.get(kind + "_requested") == "true"]
        language = form.get("source_language", "auto")
        if language not in ("auto", "da", "en", "de"):
            raise DocumentError("Vaelg et gyldigt kildesprog.")
        for kind in requested:
            if not capabilities()[kind]:
                raise DocumentError("Dokumentfunktionen er ikke aktiveret.", 409)
        file = files.get("file")
        if file is None or not file.filename:
            raise DocumentError("Vaelg et dokument.")
        content = file.read(MAX_BYTES + 1)
        filename = PurePath(file.filename.replace("\\", "/")).name[:180]
        extension = await asyncio.to_thread(validate_upload, filename, content)
        record = await app.config["DOCUMENT_SERVICE"].store.replace(
            g.document_owner, str(uuid.uuid4()), filename, content, extension
        )
        jobs = [await artifact_jobs().request(g.document_owner, record["document_id"], kind, language) for kind in requested]
        return jsonify({"document": record, "jobs": jobs, "capabilities": capabilities()}), 202

    @app.post("/api/end")
    async def end():
        body = await json_body()
        await app.config["DOCUMENT_SERVICE"].store.end(g.document_owner, document_id(body.get("document_id")))
        return jsonify({"ended": True})

    @app.post("/api/logout")
    async def logout():
        await app.config["DOCUMENT_SERVICE"].store.logout(g.document_owner)
        return jsonify({"ended": True})

    @app.post("/api/document/<active_id>/jobs")
    async def request_job(active_id):
        body = await json_body()
        kind = body.get("kind")
        if not isinstance(kind, str):
            raise DocumentError("Vaelg en dokumentfunktion.")
        result = await artifact_jobs().request(g.document_owner, document_id(active_id), kind, body.get("source_language", "auto"))
        return jsonify(result), 202

    @app.get("/api/document/<active_id>/jobs")
    async def list_jobs(active_id):
        return jsonify({"jobs": await artifact_jobs().list(g.document_owner, document_id(active_id))})

    @app.get("/api/document/<active_id>/jobs/<job_id>")
    async def get_job(active_id, job_id):
        jobs = artifact_jobs()
        return jsonify(jobs.public(await jobs.find(g.document_owner, document_id(active_id), document_id(job_id))))

    @app.post("/api/document/<active_id>/jobs/<job_id>/cancel")
    async def cancel_job(active_id, job_id):
        return jsonify(await artifact_jobs().cancel(g.document_owner, document_id(active_id), document_id(job_id)))

    @app.post("/api/document/<active_id>/jobs/<job_id>/retry")
    async def retry_job(active_id, job_id):
        jobs = artifact_jobs()
        body = await json_body()
        active_id = document_id(active_id)
        job = await jobs.find(g.document_owner, active_id, document_id(job_id))
        if job["status"] not in ("failed", "cancelled"):
            raise DocumentError("Opgaven kan ikke genstartes sikkert.", 409)
        return jsonify(await jobs.request(g.document_owner, active_id, job["kind"], body.get("source_language", job["source_language"]), retry=True)), 202

    @app.get("/api/document/<active_id>/jobs/<job_id>/summary")
    async def summary(active_id, job_id):
        return jsonify(await artifact_jobs().result(g.document_owner, document_id(active_id), document_id(job_id)))

    @app.get("/api/document/<active_id>/jobs/<job_id>/download")
    async def artifact_download(active_id, job_id):
        content = await artifact_jobs().result(g.document_owner, document_id(active_id), document_id(job_id), download=True)
        return await send_file(io.BytesIO(content), mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                               attachment_filename="dokumentresultat.docx", as_attachment=True)

    @app.post("/api/activity")
    async def activity():
        body = await json_body()
        await app.config["DOCUMENT_SERVICE"].store.active(
            g.document_owner, document_id(body.get("document_id")), touch=True
        )
        return jsonify({"active": True})

    @app.post("/api/chat")
    async def chat():
        body = await json_body()
        return jsonify(
            await app.config["DOCUMENT_SERVICE"].chat(
                g.document_owner, document_id(body.get("document_id")), body.get("question")
            )
        )

    @app.get("/api/document/<active_id>/original")
    async def original(active_id):
        active_id = document_id(active_id)
        runtime = app.config["DOCUMENT_SERVICE"]
        record = await runtime.store.active(g.document_owner, active_id)
        blob = await runtime.store.container.download_blob(
            runtime.store.prefix(g.document_owner, active_id) + "original" + record["extension"]
        )
        content = await blob.readall()
        await runtime.store.active(g.document_owner, active_id)
        return await send_file(io.BytesIO(content), attachment_filename=record["filename"], as_attachment=True)

    return app
