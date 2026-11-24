import io
import uuid
import zipfile
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pypdf import PdfWriter

from documentchat.artifacts import SummaryGenerator, export_docx, source_batches
from documentchat.extraction import ArtifactDocumentParser, ArtifactPage, coverage
from documentchat.jobs import ArtifactJobs
from documentchat.lifecycle import (
    DocumentError,
    owner_key,
    require_active,
    search_filter,
    session_active,
    validate_upload,
)
from documentchat.service import DocumentService
from documentchat.store import DocumentStore
from documentchat.translation import DocumentTranslator
from documentchat.web import create_app
from prepdocslib.page import Page


def test_documentchat_approved_inputs_keep_upload_and_image_gates(tmp_path, monkeypatch):
    from scripts.verify_document_translation import approved_cases, fixtures

    content = fixtures()[0]["content"]
    (tmp_path / "Danish.docx").write_bytes(content)
    (tmp_path / "English.pdf").write_bytes(fixtures()[1]["content"])
    with zipfile.ZipFile(tmp_path / "WithImages.docx", "w") as archive:
        archive.writestr("word/document.xml", b'<document><text>Fixture</text></document>')
        archive.writestr("word/media/image.png", b"unverified")
    report = {"cases": []}
    cases = approved_cases(tmp_path, report)
    assert {case["language"] for case in cases} == {"da", "en"}
    assert report["cases"][0]["reason"] == "embedded_images_not_verified"
    monkeypatch.setattr("scripts.verify_document_translation.MAX_BYTES", 1)
    blocked = {"cases": []}
    assert approved_cases(tmp_path, blocked) == []
    assert all(item["reason"] == "size_limit" for item in blocked["cases"])


def test_documentchat_live_fixtures_are_bounded_and_raster_only():
    import pymupdf

    from scripts.verify_document_translation import (
        CHARACTER_LIMIT,
        fixtures,
        validate_output,
    )

    cases = fixtures()
    assert {case["name"] for case in cases} == {"english-docx", "german-text-pdf", "english-scanned-pdf"}
    assert sum(case["characters"] for case in cases) < CHARACTER_LIMIT
    assert "271828" in validate_output(cases[0]["content"], table_required=True)
    for case in cases[1:]:
        with pymupdf.open(stream=case["content"], filetype="pdf") as document:
            assert len(document) == 2
            assert bool("".join(page.get_text() for page in document).strip()) == (case["name"] == "german-text-pdf")


@pytest.mark.asyncio
async def test_documentchat_office_table_without_bounding_regions(monkeypatch):
    text = "Before Product Quantity Notebook 42 After"
    table_text = "Product Quantity Notebook 42"
    table = SimpleNamespace(bounding_regions=None, spans=[SimpleNamespace(offset=7, length=len(table_text))])
    result = SimpleNamespace(content=text, tables=[table], pages=[
        SimpleNamespace(page_number=1, spans=[SimpleNamespace(offset=0, length=len(text))], words=[])
    ])
    client = MagicMock()
    client.__aenter__.return_value = client
    client.begin_analyze_document = AsyncMock(return_value=SimpleNamespace(result=AsyncMock(return_value=result)))
    monkeypatch.setattr("documentchat.extraction.DocumentIntelligenceClient", MagicMock(return_value=client))
    parser = ArtifactDocumentParser("https://example.cognitiveservices.azure.com", MagicMock())
    parser.table_to_html = MagicMock(return_value="<table><tr><td>Notebook</td><td>42</td></tr></table>")
    pages = [page async for page in parser.parse(io.BytesIO(b"fixture"))]
    assert pages[0].text == "Before <table><tr><td>Notebook</td><td>42</td></tr></table> After"
    from docx import Document

    document = Document(io.BytesIO(export_docx("Test", pages[0].text)))
    assert document.tables[0].cell(0, 1).text == "42"


def test_documentchat_deployment_uses_longer_credential_timeout(monkeypatch):
    from scripts.deploy_document_chat import DocumentChatDeployment

    credential = MagicMock()
    monkeypatch.setattr("scripts.deploy_document_chat.AzureDeveloperCliCredential", credential)
    DocumentChatDeployment("subscription", "resource-group", "tenant")
    credential.assert_called_once_with(tenant_id="tenant", process_timeout=60)


def test_documentchat_deployment_restart_targets_only_requested_application():
    from scripts.deploy_document_chat import DocumentChatDeployment

    deployment = DocumentChatDeployment.__new__(DocumentChatDeployment)
    deployment.root = "https://management.azure.com/subscriptions/sub/resourceGroups/group"
    deployment.request = MagicMock()
    deployment.restart("document-demo")
    deployment.request.assert_called_once_with(
        "POST", deployment.root + "/providers/Microsoft.Web/sites/document-demo/restart?api-version=2024-11-01"
    )


def test_documentchat_code_only_deployment_rejects_original_app(monkeypatch):
    import sys

    from scripts.deploy_document_chat import main

    monkeypatch.setattr(sys, "argv", [
        "deploy_document_chat", "--subscription", "sub", "--resource-group", "group", "--tenant", "tenant",
        "--source-app", "original", "--app-name", "original", "--storage-name", "private", "--code-only",
    ])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_documentchat_deployment_waits_for_matching_build(monkeypatch):
    from scripts.deploy_document_chat import DocumentChatDeployment

    old_health = MagicMock(status_code=200, headers={"X-DocumentChat-Build": "old-build"})
    old_health.json.return_value = {"status": "ok"}
    new_health = MagicMock(status_code=200, headers={"X-DocumentChat-Build": "new-build"})
    new_health.json.return_value = {"status": "ok"}
    request = MagicMock(side_effect=[old_health, new_health, MagicMock(status_code=401)])
    monkeypatch.setattr("scripts.deploy_document_chat.requests.get", request)
    monkeypatch.setattr("scripts.deploy_document_chat.time.sleep", MagicMock())
    deployment = DocumentChatDeployment.__new__(DocumentChatDeployment)
    deployment.verify("demo", "new-build")
    assert request.call_count == 3


def test_documentchat_deployment_status_never_uploads_or_restarts(monkeypatch, capsys):
    from scripts.deploy_document_chat import DocumentChatDeployment

    deployment = DocumentChatDeployment.__new__(DocumentChatDeployment)
    deployment.request = MagicMock(return_value=MagicMock(json=MagicMock(return_value=[{
        "id": "build-operation", "status": 4, "complete": True, "active": True, "message": "private metadata",
    }])))
    health = MagicMock(status_code=200, headers={"X-DocumentChat-Build": "expected-build"})
    health.json.return_value = {"status": "ok", "artifacts": {"enabled": False}}
    get = MagicMock(return_value=health)
    monkeypatch.setattr("scripts.deploy_document_chat.requests.get", get)
    deployment.status("document-demo")
    deployment.request.assert_called_once_with("GET", "https://document-demo.scm.azurewebsites.net/api/deployments")
    get.assert_called_once_with("https://document-demo.azurewebsites.net/healthz?artifacts=1", timeout=90, allow_redirects=False)
    output = capsys.readouterr().out
    assert "expected-build" in output
    assert "private metadata" not in output


def test_documentchat_pilot_budget_approval_is_bounded_and_never_overwrites(monkeypatch):
    import json

    from documentchat.budget import ArtifactBudget
    from scripts.deploy_document_chat import DocumentChatDeployment

    deployment = DocumentChatDeployment.__new__(DocumentChatDeployment)
    deployment.credential = MagicMock()
    container = MagicMock()
    container.__enter__.return_value = container
    factory = MagicMock(return_value=container)
    monkeypatch.setattr("scripts.deploy_document_chat.ContainerClient", factory)
    deployment.approve_pilot_budget("private-storage")
    factory.assert_called_once_with("https://private-storage.blob.core.windows.net", "ab-documentchat", deployment.credential)
    upload = container.upload_blob.call_args
    assert upload.args[0] == ArtifactBudget.path
    ledger = json.loads(upload.args[1])
    assert ArtifactBudget(None).valid(ledger)
    assert ledger["limit_ore"] - ledger["overhead_ore"] == 10_000
    assert ledger["reservations"] == {}
    assert upload.kwargs["overwrite"] is False


@pytest.mark.parametrize("blocked", ["wrong_user", "active_document", "disabled"])
def test_documentchat_live_browser_stops_before_upload_when_unsafe(monkeypatch, tmp_path, blocked):
    from scripts.verify_document_translation import verify_app_browser

    tenant = "141346e9-efbf-4062-ab81-a8819181ba24"
    browser = MagicMock()
    context = browser.new_context.return_value
    page = context.new_page.return_value
    playwright = MagicMock()
    playwright.chromium.launch.return_value = browser
    manager = MagicMock()
    manager.__enter__.return_value = playwright
    monkeypatch.setattr("playwright.sync_api.sync_playwright", MagicMock(return_value=manager))

    def respond(url, **kwargs):
        if url.endswith("/.auth/me"):
            value = [{"user_claims": [
                {"typ": "tid", "val": tenant},
                {"typ": "oid", "val": "other-user" if blocked == "wrong_user" else "d4b92084-5272-47eb-a4e7-65ebeaaa6c42"},
            ]}]
        else:
            value = {"document": {"document_id": "existing"} if blocked == "active_document" else None,
                     "capabilities": {"summary": blocked != "disabled", "translation": blocked != "disabled"}}
        return SimpleNamespace(ok=True, json=lambda: value)

    context.request.get.side_effect = respond
    with pytest.raises(RuntimeError):
        verify_app_browser(SimpleNamespace(tenant=tenant, output=tmp_path), {"cases": []})
    page.get_by_label.assert_not_called()
    context.request.post.assert_not_called()
    browser.close.assert_called_once()


def test_documentchat_replacement_is_immediate():
    previous_id = str(uuid.uuid4())
    current_id = str(uuid.uuid4())
    session = {"document_id": current_id, "last_activity": datetime.now(timezone.utc).isoformat()}
    assert session_active(session, current_id)
    with pytest.raises(DocumentError, match="erstattet"):
        require_active(session, previous_id)


@pytest.mark.parametrize(
    "idle_seconds, expected_active",
    [(30 * 60, True), (119 * 60, True), (120 * 60 - 1, True), (120 * 60, False), (121 * 60, False)],
)
def test_documentchat_expiry_boundary(idle_seconds, expected_active):
    now = datetime.now(timezone.utc)
    active_id = str(uuid.uuid4())
    session = {"document_id": active_id, "last_activity": (now - timedelta(seconds=idle_seconds)).isoformat()}
    assert session_active(session, active_id, now) is expected_active


def test_documentchat_filter_binds_owner_and_document():
    active_id = str(uuid.uuid4())
    owner = owner_key("tenant", "first-user")
    assert owner != owner_key("tenant", "second-user")
    assert owner != owner_key("other-tenant", "first-user")
    assert search_filter(owner, active_id) == f"owner_id eq '{owner}' and document_id eq '{active_id}'"
    with pytest.raises(DocumentError):
        search_filter(owner, "' or true")
    with pytest.raises(DocumentError):
        search_filter("' or true", active_id)


@pytest.mark.parametrize("session", [{}, {"document_id": "current", "last_activity": "invalid"}])
def test_documentchat_invalid_state_fails_closed(session):
    assert not session_active(session, "current")


@pytest.mark.parametrize(
    "filename,content",
    [("test.exe", b"content"), ("test.pdf", b"not a pdf"), ("test.docx", b"not a zip"), ("test.pdf", b"")],
)
def test_documentchat_rejects_invalid_files(filename, content):
    with pytest.raises(DocumentError):
        validate_upload(filename, content)


@pytest.mark.asyncio
async def test_documentchat_api_requires_authentication():
    app = create_app(service=MagicMock())
    response = await app.test_client().get("/api/session")
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_documentchat_rejects_cross_origin_mutation():
    authenticate = AsyncMock(return_value=owner_key("tenant", "user"))
    app = create_app(service=MagicMock(), authenticate=authenticate)
    response = await app.test_client().post(
        "/api/end", json={"document_id": str(uuid.uuid4())}, headers={"Origin": "https://attacker.example"}
    )
    assert response.status_code == 403
    authenticate.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_api_uses_authenticated_owner():
    owner = owner_key("tenant", "real-user")
    active_id = str(uuid.uuid4())
    runtime = MagicMock()
    runtime.chat = AsyncMock(return_value={"content": "Answer", "sources": []})
    app = create_app(service=runtime, authenticate=AsyncMock(return_value=owner))
    response = await app.test_client().post(
        "/api/chat",
        json={"document_id": active_id, "owner": "attacker-controlled", "question": "Question"},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 200
    runtime.chat.assert_awaited_once_with(owner, active_id, "Question")


@pytest.mark.asyncio
async def test_documentchat_logout_ends_current_owner_without_stale_document_id():
    owner = owner_key("tenant", "real-user")
    runtime = MagicMock()
    runtime.store.logout = AsyncMock()
    app = create_app(service=runtime, authenticate=AsyncMock(return_value=owner))
    response = await app.test_client().post("/api/logout", json={"document_id": "stale-id", "owner": "attacker"}, headers={"Origin": "http://localhost"})
    assert response.status_code == 200
    runtime.store.logout.assert_awaited_once_with(owner)


@pytest.mark.asyncio
async def test_documentchat_artifact_api_is_disabled_by_default():
    app = create_app(service=MagicMock(), authenticate=AsyncMock(return_value=owner_key("tenant", "user")))
    response = await app.test_client().post(f"/api/document/{uuid.uuid4()}/jobs", json={"kind": "summary"}, headers={"Origin": "http://localhost"})
    assert response.status_code == 404


def test_documentchat_accepts_scanned_pdf_and_rejects_over_100_pages():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    content = io.BytesIO()
    writer.write(content)
    assert validate_upload("scan.pdf", content.getvalue()) == ".pdf"
    for page_number in range(100):
        writer.add_blank_page(width=100, height=100)
    content = io.BytesIO()
    writer.write(content)
    with pytest.raises(DocumentError, match="100 sider"):
        validate_upload("large.pdf", content.getvalue())


def test_documentchat_checks_word_content_before_ocr():
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:t>'
            + "a" * 200_001
            + "</w:t></w:document>",
        )
    with pytest.raises(DocumentError, match="200.000"):
        validate_upload("large.docx", content.getvalue())


@asynccontextmanager
async def documentchat_test_lock(*args, **kwargs):
    yield MagicMock()


async def documentchat_test_results(items):
    for item in items:
        yield item


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_documentchat_persists_full_extraction_only_when_enabled(enabled):
    owner = owner_key("tenant", "user")
    active_id = str(uuid.uuid4())
    prefix = f"documents/{owner}/{active_id}/"
    store = MagicMock()
    store.active = AsyncMock()
    store.write = AsyncMock()
    store.prefix.return_value = prefix
    source = MagicMock()
    source.readall = AsyncMock(return_value=b"original document")
    store.container.download_blob = AsyncMock(return_value=source)
    pages = [Page(0, 0, "Beginning of the document."), Page(1, 26, "Final important conclusion.")]
    parser = MagicMock()
    parser.parse.side_effect = lambda content: documentchat_test_results(pages)
    search = MagicMock()
    search.upload_documents = AsyncMock(return_value=[MagicMock(succeeded=True)])
    runtime = DocumentService(store, search, MagicMock(), parser, "chat", "embedding", enabled)
    runtime.embed = AsyncMock(return_value=[[0.0] * 3072] * 10)
    await runtime.ingest({"owner": owner, "document_id": active_id, "extension": ".pdf", "status": "queued"}, MagicMock())
    extractions = [call.args[1] for call in store.write.await_args_list if call.args[0] == prefix + "extraction.json"]
    if enabled:
        assert len(extractions) == 1
        assert extractions[0]["document_id"] == active_id
        assert extractions[0]["pages"] == [{"page": page.page_num + 1, "text": page.text} for page in pages]
        assert extractions[0]["sources"]
        assert len(extractions[0]["original_hash"]) == 64
    else:
        assert not extractions


def test_documentchat_docx_export_preserves_tables_and_sources():
    from docx import Document

    content = export_docx("Resume", "En pointe [1].<table><tr><td>Belob</td><td>2400</td></tr></table>",
                          [{"number": 1, "page": 3, "text": "Praemien er 2400."}])
    document = Document(io.BytesIO(content))
    assert document.tables[0].cell(0, 1).text == "2400"
    assert any("Side 3" in paragraph.text for paragraph in document.paragraphs)


def test_documentchat_source_batches_never_drop_end_of_document():
    sources = [{"number": number, "text": "x" * 5000} for number in range(1, 21)]
    assert [source for batch in source_batches(sources) for source in batch] == sources


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,accepted", [
    ('{"paragraphs":[{"text":"Hovedpointen.","source_ids":[1]}]}', True),
    ('{"paragraphs":[{"text":"Ingen kilder.","source_ids":[]}]}', False),
    ('{"paragraphs":[{"text":"Ukendt.","source_ids":[99]}]}', False),
    ('{"paragraphs":[{"text":"Skjult henvisning [99].","source_ids":[1]}]}', False),
    ('{"paragraphs":[{"text":"Forkert type.","source_ids":[true]}]}', False),
    ('{"paragraphs":[]}', False),
    ('Ugyldigt JSON', False),
])
async def test_documentchat_summary_validates_original_sources(answer, accepted):
    openai = MagicMock()
    openai.with_options.return_value = openai
    response = MagicMock()
    response.choices[0].message.content = answer
    response.choices[0].finish_reason = "stop"
    response.usage = None
    openai.chat.completions.create = AsyncMock(return_value=response)
    checkpoint = AsyncMock()
    generator = SummaryGenerator(openai, "chat")
    extraction = {"sources": [{"number": 1, "page": 1, "text": "The important conclusion."}]}
    if accepted:
        result = await generator.generate(extraction, checkpoint)
        assert result["sources"] == extraction["sources"]
        assert result["content"] == "Hovedpointen. [1]"
    else:
        with pytest.raises(DocumentError):
            await generator.generate(extraction, checkpoint)


@pytest.fixture
def documentchat_artifact_runtime():
    from documentchat.budget import ArtifactBudget
    from documentchat.lifecycle import utc_now

    values = {ArtifactBudget.path: {
        "version": 1, "month": utc_now().strftime("%Y-%m"), "rate_card": ArtifactBudget.rate_card,
        "limit_ore": 100_000, "overhead_ore": 10_000, "reservations": {},
    }}
    store = MagicMock()
    store.active = AsyncMock(return_value={"status": "ready", "extension": ".pdf"})
    store.prefix.side_effect = lambda owner, active_id: f"documents/{owner}/{active_id}/"
    store.read = AsyncMock(side_effect=lambda path: deepcopy(values.get(path, {})))
    store.lock.side_effect = documentchat_test_lock

    async def write(path, value, lease=None):
        values[path] = deepcopy(value)

    store.write = AsyncMock(side_effect=write)
    store.container.upload_blob = AsyncMock()
    runtime = MagicMock(store=store, enable_artifacts=True, chat_model="gpt-5.4-mini")
    runtime.openai.with_options.return_value = runtime.openai
    runtime.openai.chat.completions.create = AsyncMock()
    return runtime, values


@pytest.mark.asyncio
async def test_documentchat_budget_retains_reservations_and_blocks_exhaustion(documentchat_artifact_runtime):
    from documentchat.budget import ArtifactBudget
    from documentchat.lifecycle import utc_now

    runtime, values = documentchat_artifact_runtime
    budget = ArtifactBudget(runtime.store)
    values[budget.path] = {
        "version": 1, "month": utc_now().strftime("%Y-%m"), "rate_card": budget.rate_card,
        "limit_ore": 1000, "overhead_ore": 100, "reservations": {},
    }
    first = await budget.reserve(400, "summary")
    second = await budget.reserve(500, "translation")
    assert first != second
    assert sum(item["amount_ore"] for item in values[budget.path]["reservations"].values()) == 900
    with pytest.raises(DocumentError, match="opbrugt"):
        await ArtifactBudget(runtime.store).reserve(1, "language")
    assert len(values[budget.path]["reservations"]) == 2
    runtime.openai.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_status", [200, 403])
async def test_documentchat_extended_readiness_is_read_only(documentchat_artifact_runtime, provider_status):
    runtime, values = documentchat_artifact_runtime
    runtime.enable_artifacts = False
    runtime.store.container.get_container_properties = AsyncMock()
    runtime.search.search = AsyncMock(return_value=MagicMock(get_count=AsyncMock(return_value=0)))
    translator = MagicMock(
        source=MagicMock(get_container_properties=AsyncMock()),
        target=MagicMock(get_container_properties=AsyncMock()),
        request=AsyncMock(return_value=httpx.Response(provider_status,
            request=httpx.Request("GET", "https://translator.cognitiveservices.azure.com/translator/document/batches"),
            json={"value": [{"id": "must-not-be-published"}]})),
    )
    runtime.artifact_jobs = ArtifactJobs(runtime, translator)
    app = create_app(service=runtime)
    client = app.test_client()
    basic = await client.get("/healthz")
    assert basic.status_code == 200
    assert await basic.get_json() == {"status": "ok"}
    translator.request.assert_not_called()
    extended = await client.get("/healthz?artifacts=1")
    assert extended.status_code == (200 if provider_status == 200 else 503)
    if provider_status == 200:
        assert await extended.get_json() == {"status": "ok", "artifacts": {
            "enabled": False, "translator_access": True, "staging_access": True, "budget_approved": True,
        }}
    assert "must-not-be-published" not in await extended.get_data(as_text=True)
    translator.request.assert_awaited_once_with("GET", "?%24maxpagesize=1")
    runtime.store.write.assert_not_called()
    runtime.store.container.upload_blob.assert_not_called()
    runtime.openai.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_extended_readiness_requires_translator_configuration(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    runtime.store.container.get_container_properties = AsyncMock()
    runtime.search.search = AsyncMock(return_value=MagicMock(get_count=AsyncMock(return_value=0)))
    runtime.artifact_jobs = ArtifactJobs(runtime)
    app = create_app(service=runtime)
    response = await app.test_client().get("/healthz?artifacts=1")
    assert response.status_code == 503
    runtime.store.write.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_budget_serializes_reservations_across_instances(documentchat_artifact_runtime):
    import asyncio

    from documentchat.budget import ArtifactBudget

    runtime, values = documentchat_artifact_runtime
    budget = ArtifactBudget(runtime.store)
    values[budget.path].update(limit_ore=600, overhead_ore=0)
    mutex, second_waiting = asyncio.Lock(), asyncio.Event()
    original_read = runtime.store.read
    waiting = 0

    @asynccontextmanager
    async def lock(path, create=False):
        nonlocal waiting
        assert path == budget.path and create
        waiting += 1
        if waiting == 2:
            second_waiting.set()
        async with mutex:
            yield "budget-lease"

    async def read(path):
        result = await original_read(path)
        await second_waiting.wait()
        return result

    runtime.store.lock.side_effect = lock
    runtime.store.read = AsyncMock(side_effect=read)
    results = await asyncio.wait_for(asyncio.gather(
        budget.reserve(400, "summary"), ArtifactBudget(runtime.store).reserve(400, "translation"),
        return_exceptions=True,
    ), timeout=2)
    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, DocumentError) for result in results) == 1
    assert len(values[budget.path]["reservations"]) == 1
    assert runtime.store.write.await_args.args[2] == "budget-lease"


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {}, {"month": "2020-01"}, {"rate_card": "unknown"}, {"limit_ore": 100001},
    {"limit_ore": True}, {"version": True}, {"overhead_ore": -1}, {"reservations": []},
    {"reservations": {"previous": {"amount_ore": -1, "purpose": "summary"}}},
])
async def test_documentchat_budget_rejects_missing_or_invalid_approval(documentchat_artifact_runtime, override):
    from documentchat.budget import ArtifactBudget
    from documentchat.lifecycle import utc_now

    runtime, values = documentchat_artifact_runtime
    budget = ArtifactBudget(runtime.store)
    values.pop(budget.path)
    if override:
        values[budget.path] = {
            "version": 1, "month": utc_now().strftime("%Y-%m"), "rate_card": budget.rate_card,
            "limit_ore": 1000, "overhead_ore": 100, "reservations": {}, **override,
        }
    with pytest.raises(DocumentError, match="ikke godkendt"):
        await budget.reserve(1, "summary")
    runtime.store.write.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,language", [("summary", "auto"), ("translation", "en"), ("translation", "auto")])
async def test_documentchat_missing_budget_blocks_paid_artifacts(documentchat_artifact_runtime, kind, language):
    from documentchat.budget import ArtifactBudget

    runtime, values = documentchat_artifact_runtime
    values.pop(ArtifactBudget.path)
    values["documents/owner/document/extraction.json"] = {
        "coverage": {"status": "adequate"}, "sources": [{"number": 1, "page": 1, "text": "Conclusion."}],
        "pages": [{"text": "Conclusion."}],
    }
    translator = MagicMock(language=AsyncMock(return_value="en"), stage=AsyncMock(), submit=AsyncMock())
    jobs = ArtifactJobs(runtime, translator)
    await jobs.request("owner", "document", kind, language)
    await jobs.process("owner", "document", kind)
    assert values[jobs.path("owner", "document", kind)]["status"] == "failed"
    runtime.openai.chat.completions.create.assert_not_called()
    translator.stage.assert_not_called()
    translator.submit.assert_not_called()
    runtime.store.container.upload_blob.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read", "write"])
async def test_documentchat_budget_storage_failure_cannot_start_paid_call(documentchat_artifact_runtime, failure):
    from documentchat.budget import ArtifactBudget

    runtime, values = documentchat_artifact_runtime
    values["documents/owner/document/extraction.json"] = {
        "coverage": {"status": "adequate"}, "sources": [{"number": 1, "page": 1, "text": "Conclusion."}],
    }
    jobs = ArtifactJobs(runtime)
    await jobs.request("owner", "document", "summary")
    original = getattr(runtime.store, failure).side_effect

    async def unavailable(path, *args):
        if path == ArtifactBudget.path:
            raise OSError("budget storage unavailable")
        result = original(path, *args)
        return await result if failure == "write" else result

    getattr(runtime.store, failure).side_effect = unavailable
    await jobs.process("owner", "document", "summary")
    runtime.openai.chat.completions.create.assert_not_called()
    runtime.store.container.upload_blob.assert_not_called()
    assert not values[ArtifactBudget.path]["reservations"]


@pytest.mark.asyncio
async def test_documentchat_translation_timeout_retains_linked_financial_reservation(documentchat_artifact_runtime):
    from documentchat.budget import ArtifactBudget

    runtime, values = documentchat_artifact_runtime
    values["documents/owner/document/extraction.json"] = {
        "coverage": {"status": "adequate"}, "pages": [{"text": "Original document."}],
    }
    source = MagicMock(url="https://private.blob.core.windows.net/source", upload_blob=AsyncMock())
    target = MagicMock(url="https://private.blob.core.windows.net/target")
    credential = MagicMock(get_token=AsyncMock(return_value=MagicMock(token="test-token")))
    requests = []

    def handle(request):
        assert values[ArtifactBudget.path]["reservations"]
        requests.append(request)
        raise httpx.ReadTimeout("accepted but response lost", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        translator = DocumentTranslator("translator", credential, source, target, runtime.store, runtime.openai, runtime.chat_model, client)
        jobs = ArtifactJobs(runtime, translator)
        job = await jobs.request("owner", "document", "translation", "en")
        await jobs.start_translation("owner", "document")
        await jobs.start_translation("owner", "document")
        operation = values[translator.operation_path("owner", "document", job["job_id"])]
    assert operation["status"] == "submission_unknown"
    reservations = values[ArtifactBudget.path]["reservations"]
    assert len(reservations) == 1
    assert reservations[operation["budget_reservation_id"]]["purpose"] == "translation"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_documentchat_explicit_danish_translation_needs_no_financial_reservation(documentchat_artifact_runtime):
    from documentchat.budget import ArtifactBudget

    runtime, values = documentchat_artifact_runtime
    values.pop(ArtifactBudget.path)
    values["documents/owner/document/extraction.json"] = {"coverage": {"status": "adequate"}}
    translator = MagicMock(language=AsyncMock(return_value="da"), stage=AsyncMock(), submit=AsyncMock())
    jobs = ArtifactJobs(runtime, translator)
    await jobs.request("owner", "document", "translation", "da")
    await jobs.start_translation("owner", "document")
    assert values[jobs.path("owner", "document", "translation")]["status"] == "not_needed"
    assert ArtifactBudget.path not in values
    translator.stage.assert_not_called()
    translator.submit.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_budget_rejects_unpriced_model(documentchat_artifact_runtime):
    from documentchat.budget import ArtifactBudget

    runtime, values = documentchat_artifact_runtime
    with pytest.raises(DocumentError, match="prisgrundlag"):
        await ArtifactBudget(runtime.store).reserve_model("unpriced-model", "summary")
    assert not values[ArtifactBudget.path]["reservations"]


@pytest.mark.asyncio
async def test_documentchat_summary_timeout_retains_financial_reservation(documentchat_artifact_runtime):
    from documentchat.budget import ArtifactBudget

    runtime, values = documentchat_artifact_runtime
    values["documents/owner/document/extraction.json"] = {
        "coverage": {"status": "adequate"}, "sources": [{"number": 1, "page": 1, "text": "Conclusion."}],
    }
    runtime.openai.chat.completions.create.side_effect = TimeoutError("provider outcome unknown")
    jobs = ArtifactJobs(runtime)
    await jobs.request("owner", "document", "summary")
    await jobs.process("owner", "document", "summary")
    assert values[jobs.path("owner", "document", "summary")]["status"] == "submission_unknown"
    reservations = values[ArtifactBudget.path]["reservations"]
    assert len(reservations) == 1
    assert next(iter(reservations.values())) == {"amount_ore": 520, "purpose": "summary"}
    await jobs.process("owner", "document", "summary")
    assert len(values[ArtifactBudget.path]["reservations"]) == 1
    assert runtime.openai.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_documentchat_summary_budget_exhaustion_between_batches_publishes_nothing(documentchat_artifact_runtime):
    from documentchat.budget import ArtifactBudget

    runtime, values = documentchat_artifact_runtime
    values[ArtifactBudget.path].update(limit_ore=520, overhead_ore=0)
    values["documents/owner/document/extraction.json"] = {
        "coverage": {"status": "adequate"},
        "sources": [{"number": number, "page": number, "text": "x" * 13000} for number in (1, 2)],
    }
    response = MagicMock()
    response.choices[0].message.content = '{"paragraphs":[{"text":"Delens hovedpointe.","source_ids":[1]}]}'
    response.choices[0].finish_reason = "stop"
    response.usage = None
    runtime.openai.chat.completions.create.return_value = response
    jobs = ArtifactJobs(runtime)
    await jobs.request("owner", "document", "summary")
    await jobs.process("owner", "document", "summary")
    assert values[jobs.path("owner", "document", "summary")]["status"] == "failed"
    assert runtime.openai.chat.completions.create.await_count == 1
    assert len(values[ArtifactBudget.path]["reservations"]) == 1
    runtime.store.container.upload_blob.assert_not_called()


def test_documentchat_pdf_coverage_rejects_missing_and_uncertain_pages():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    content = io.BytesIO()
    writer.write(content)
    first = ArtifactPage(0, 0, "Reliable original text", words=10, known_words=10, uncertain_words=0)
    second = ArtifactPage(1, 22, "Final original conclusion", words=10, known_words=10, uncertain_words=0)
    assert coverage(content.getvalue(), ".pdf", [first, second])["status"] == "adequate"
    assert "missing_pages" in coverage(content.getvalue(), ".pdf", [first])["reasons"]
    second.uncertain_words = 4
    assert "uncertain_ocr" in coverage(content.getvalue(), ".pdf", [first, second])["reasons"]


def test_documentchat_word_coverage_rejects_missing_conclusion():
    from docx import Document

    content = io.BytesIO()
    document = Document()
    document.add_paragraph("Beginning of the document.")
    document.add_paragraph("Final essential conclusion.")
    document.save(content)
    page = ArtifactPage(0, 0, "Beginning of the document. Final essential conclusion.")
    assert coverage(content.getvalue(), ".docx", [page])["status"] == "adequate"
    page.text = "Beginning of the document."
    assert coverage(content.getvalue(), ".docx", [page])["status"] == "insufficient"


@pytest.mark.asyncio
async def test_documentchat_artifact_requests_are_durably_idempotent(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    first = await ArtifactJobs(runtime).request("owner", "document", "summary")
    second = await ArtifactJobs(runtime).request("owner", "document", "summary")
    assert first["job_id"] == second["job_id"]
    assert first["status"] == "queued"
    assert len(values) == 2
    runtime.openai.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_interrupted_artifact_is_not_resubmitted(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    jobs = ArtifactJobs(runtime)
    await jobs.request("owner", "document", "summary")
    values[jobs.path("owner", "document", "summary")]["status"] = "running"
    await ArtifactJobs(runtime).process("owner", "document", "summary")
    assert values[jobs.path("owner", "document", "summary")]["status"] == "submission_unknown"
    runtime.openai.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_insufficient_coverage_blocks_before_paid_call(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    jobs = ArtifactJobs(runtime)
    await jobs.request("owner", "document", "summary")
    await jobs.process("owner", "document", "summary")
    assert values[jobs.path("owner", "document", "summary")]["status"] == "failed"
    runtime.openai.chat.completions.create.assert_not_called()
    runtime.store.container.upload_blob.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_cancelled_artifact_cannot_publish(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    jobs = ArtifactJobs(runtime)
    job = await jobs.request("owner", "document", "summary")
    values["documents/owner/document/extraction.json"] = {
        "coverage": {"status": "adequate"},
        "sources": [{"number": 1, "page": 1, "text": "The conclusion."}],
    }

    async def respond(**kwargs):
        await jobs.cancel("owner", "document", job["job_id"])
        response = MagicMock()
        response.choices[0].message.content = '{"paragraphs":[{"text":"Konklusion.","source_ids":[1]}]}'
        response.choices[0].finish_reason = "stop"
        response.usage = None
        return response

    runtime.openai.chat.completions.create.side_effect = respond
    await jobs.process("owner", "document", "summary")
    assert values[jobs.path("owner", "document", "summary")]["status"] == "cancelled"
    runtime.store.container.upload_blob.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_ready_summary_has_matching_json_and_real_docx(documentchat_artifact_runtime):
    from docx import Document

    runtime, values = documentchat_artifact_runtime
    jobs = ArtifactJobs(runtime)
    job = await jobs.request("owner", "document", "summary")
    sources = [{"number": 1, "page": 1, "text": "The important conclusion."}]
    values["documents/owner/document/extraction.json"] = {"coverage": {"status": "adequate"}, "sources": sources}
    response = MagicMock()
    response.choices[0].message.content = '{"paragraphs":[{"text":"Den vigtige konklusion.","source_ids":[1]}]}'
    response.choices[0].finish_reason = "stop"
    response.usage = None
    runtime.openai.chat.completions.create.return_value = response
    await jobs.process("owner", "document", "summary")
    assert values[jobs.path("owner", "document", "summary")]["status"] == "ready"
    result = await jobs.result("owner", "document", job["job_id"])
    uploaded = runtime.store.container.upload_blob.await_args.args[1]
    document = Document(io.BytesIO(uploaded))
    assert any(paragraph.text == result["content"] for paragraph in document.paragraphs)
    assert result["sources"] == sources
    runtime.openai.with_options.assert_called_with(max_retries=0, timeout=120.0)


@pytest.mark.asyncio
async def test_documentchat_summary_reads_every_batch_including_last():
    import json

    openai = MagicMock()
    openai.with_options.return_value = openai
    calls = []

    async def respond(**kwargs):
        context = kwargs["messages"][-1]["content"]
        calls.append(context)
        if len(calls) < 3:
            batch = json.loads(context)
            expected = 11 if len(calls) == 1 else 22
            other = 22 if len(calls) == 1 else 11
            assert batch[0]["number"] == expected
            assert f"[{expected}]" in kwargs["messages"][0]["content"]
            assert f"[{other}]" not in kwargs["messages"][0]["content"]
            schema = kwargs["response_format"]["json_schema"]
            assert schema["strict"] is True
            assert schema["schema"]["properties"]["paragraphs"]["items"]["properties"]["source_ids"]["items"]["enum"] == [expected]
        response = MagicMock()
        response.choices[0].message.content = json.dumps({"paragraphs": [{"text": "Begyndelse og slutning." if len(calls) == 3 else "Vigtig oplysning.",
                                                                     "source_ids": [11, 22] if len(calls) == 3 else [11 if len(calls) == 1 else 22]}]})
        response.choices[0].finish_reason = "stop"
        response.usage = None
        return response

    openai.chat.completions.create = AsyncMock(side_effect=respond)
    extraction = {"sources": [{"number": 11, "page": 1, "text": "beginning " * 2000 + "Internal reference [99]."}, {"number": 22, "page": 100, "text": "final conclusion " * 1000}]}
    result = await SummaryGenerator(openai, "chat").generate(extraction, AsyncMock())
    assert "beginning" in calls[0]
    assert "final conclusion" in calls[1]
    assert len(calls) == 3
    assert [source["number"] for source in result["sources"]] == [11, 22]


@pytest.mark.asyncio
async def test_documentchat_summary_rejects_citation_from_another_batch():
    openai = MagicMock()
    openai.with_options.return_value = openai
    response = MagicMock()
    response.choices[0].message.content = '{"paragraphs":[{"text":"Oplysning fra forkert del.","source_ids":[2]}]}'
    response.choices[0].finish_reason = "stop"
    openai.chat.completions.create = AsyncMock(return_value=response)
    extraction = {"sources": [{"number": number, "page": number, "text": "x" * 13000} for number in (1, 2)]}
    with pytest.raises(DocumentError):
        await SummaryGenerator(openai, "chat").generate(extraction, AsyncMock())
    assert openai.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_documentchat_artifact_lookup_requires_current_owner(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    jobs = ArtifactJobs(runtime)
    job = await jobs.request("owner", "document", "summary")
    runtime.store.active.side_effect = DocumentError("Dokumentet findes ikke.", 404)
    with pytest.raises(DocumentError):
        await jobs.result("other-owner", "document", job["job_id"])
    runtime.store.container.download_blob.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_documentchat_translator_persists_submission_and_uses_private_file_urls(documentchat_artifact_runtime, accepted):
    runtime, values = documentchat_artifact_runtime
    provider_id = str(uuid.uuid4())
    requests = []

    def handle(request):
        requests.append(request)
        assert any(value.get("status") == "submission_unknown" for value in values.values())
        if not accepted:
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(202, headers={"operation-location": f"https://translator.cognitiveservices.azure.com/translator/document/batches/{provider_id}?api-version=2026-03-01"})

    source, target = MagicMock(url="https://private.blob.core.windows.net/ab-documentchat-translation-source"), MagicMock(url="https://private.blob.core.windows.net/ab-documentchat-translation-target")
    source.upload_blob = AsyncMock()
    credential = MagicMock(get_token=AsyncMock(return_value=MagicMock(token="test-token")))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        translator = DocumentTranslator("translator", credential, source, target, runtime.store, runtime.openai, "chat", client)
        job = {"job_id": str(uuid.uuid4()), "source_language": "en"}
        operation = await translator.stage("owner", "document", job, {"pages": [{"text": "Original document."}]})
        if accepted:
            result = await translator.submit(operation)
            assert result["provider_id"] == provider_id
            assert result["status"] == "submitted"
        else:
            with pytest.raises(httpx.ReadTimeout):
                await translator.submit(operation)
            assert values[translator.operation_path("owner", "document", job["job_id"])]["status"] == "submission_unknown"
    assert len(requests) == 1
    import json
    body = json.loads(requests[0].content)
    assert body["inputs"][0]["storageType"] == "File"
    assert body["inputs"][0]["targets"][0]["language"] == "da"
    assert "?" not in body["inputs"][0]["source"]["sourceUrl"]
    assert requests[0].headers["Authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_documentchat_translator_reconciles_lost_submission_across_both_lists(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    provider_id = str(uuid.uuid4())
    requests = []
    source = MagicMock(url="https://private.blob.core.windows.net/ab-documentchat-translation-source", upload_blob=AsyncMock())
    target = MagicMock(url="https://private.blob.core.windows.net/ab-documentchat-translation-target")
    credential = MagicMock(get_token=AsyncMock(return_value=MagicMock(token="test-token")))
    operation = {}

    def handle(request):
        requests.append(request)
        assert request.url.params["api-version"] == "2026-03-01"
        if request.method == "POST":
            raise httpx.ReadTimeout("accepted but response lost", request=request)
        assert request.method == "GET"
        if request.url.path.endswith("/documents"):
            if request.url.params.get("$skip") == "1":
                return httpx.Response(200, json={"value": [{"translatedDocumentUrl": target.url + "/" + operation["blob_prefix"] + "result.docx"}]})
            return httpx.Response(200, json={"value": [{"translatedDocumentUrl": target.url + "/another-owner/result.docx"}],
                "@nextLink": f"https://translator.cognitiveservices.azure.com/translator/document/batches/{provider_id}/documents?$skip=1"})
        if request.url.params.get("$skip") == "1":
            assert request.url.params["continuation"] == "a+b/="
            return httpx.Response(200, json={"value": [{"id": provider_id, "createdDateTimeUtc": operation["created_at"]}]})
        return httpx.Response(200, json={"value": [{"id": str(uuid.uuid4()), "createdDateTimeUtc": "2000-01-01T00:00:00Z"}],
            "@nextLink": "?$skip=1&continuation=a%2Bb%2F%3D&api-version=1900-01-01"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        translator = DocumentTranslator("translator", credential, source, target, runtime.store, runtime.openai, "chat", client)
        job = {"job_id": str(uuid.uuid4()), "source_language": "en"}
        operation = await translator.stage("owner", "document", job, {"pages": [{"text": "Original document."}]})
        with pytest.raises(httpx.ReadTimeout):
            await translator.submit(operation)
        path = translator.operation_path("owner", "document", job["job_id"])
        assert values[path]["status"] == "submission_unknown"
        restarted = DocumentTranslator("translator", credential, source, target, runtime.store, runtime.openai, "chat", client)
        recovered = await restarted.reconcile(deepcopy(values[path]))
        assert recovered["provider_id"] == provider_id
        assert values[path]["status"] == "submitted"
    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET", "GET"]
    source.upload_blob.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("next_link", [
    "https://untrusted.example/translator/document/batches?$skip=1",
    "//untrusted.example/translator/document/batches?$skip=1",
    "http://translator.cognitiveservices.azure.com/translator/document/batches?$skip=1",
    "https://translator.cognitiveservices.azure.com/another-list?$skip=1",
    "https://user:password@translator.cognitiveservices.azure.com/translator/document/batches?$skip=1",
    "https://translator.cognitiveservices.azure.com/translator/document/batches#fragment",
    "?api-version=2026-03-01",
    42,
])
async def test_documentchat_translator_rejects_unsafe_or_cyclic_paging(documentchat_artifact_runtime, next_link):
    runtime, values = documentchat_artifact_runtime
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.host == "translator.cognitiveservices.azure.com"
        assert request.method == "GET"
        return httpx.Response(200, json={"value": [], "@nextLink": next_link})

    credential = MagicMock(get_token=AsyncMock(return_value=MagicMock(token="test-token")))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        translator = DocumentTranslator("translator", credential, MagicMock(), MagicMock(), runtime.store, runtime.openai, "chat", client)
        with pytest.raises(RuntimeError, match="paging"):
            [item async for item in translator.list_items("")]
    assert len(requests) <= 2
    runtime.store.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_documentchat_translator_bounds_paging(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"value": [], "@nextLink": f"?$skip={len(requests)}"})

    credential = MagicMock(get_token=AsyncMock(return_value=MagicMock(token="test-token")))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        translator = DocumentTranslator("translator", credential, MagicMock(), MagicMock(), runtime.store, runtime.openai, "chat", client)
        with pytest.raises(RuntimeError, match="paging limit"):
            [item async for item in translator.list_items("")]
    assert len(requests) == 100


@pytest.mark.asyncio
async def test_documentchat_danish_translation_never_submits(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    translator = MagicMock(language=AsyncMock(return_value="da"), stage=AsyncMock(), submit=AsyncMock())
    jobs = ArtifactJobs(runtime, translator)
    await jobs.request("owner", "document", "translation", "da")
    values["documents/owner/document/extraction.json"] = {"coverage": {"status": "adequate"}, "pages": [{"text": "Dansk originaltekst."}]}
    await jobs.start_translation("owner", "document")
    assert values[jobs.path("owner", "document", "translation")]["status"] == "not_needed"
    translator.stage.assert_not_called()
    translator.submit.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_translator_rejects_partial_result_before_download(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    target = MagicMock(download_blob=AsyncMock())
    translator = DocumentTranslator("translator", MagicMock(), MagicMock(), target, runtime.store, runtime.openai, "chat", MagicMock())
    with pytest.raises(DocumentError):
        await translator.output({"provider_status": "Succeeded", "summary": {"total": 2, "success": 1, "failed": 1}})
    target.download_blob.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("still_active", [True, False])
async def test_documentchat_translation_recovers_and_cleans_late_output(documentchat_artifact_runtime, still_active):
    runtime, values = documentchat_artifact_runtime
    job_id = str(uuid.uuid4())
    operation_path = f"translation-operations/owner/document/{job_id}.json"
    operation = {"owner": "owner", "document_id": "document", "job_id": job_id, "provider_id": str(uuid.uuid4()),
                 "status": "submitted", "created_at": datetime.now(timezone.utc).isoformat()}

    async def poll(current, cancel=False):
        assert cancel is not still_active
        return {**current, "status": "terminal", "provider_status": "Succeeded"}

    translator = MagicMock(poll=AsyncMock(side_effect=poll), output=AsyncMock(return_value=export_docx("Dansk", "Hele dokumentet.")), delete_staging=AsyncMock())
    jobs = ArtifactJobs(runtime, translator)
    values[operation_path] = operation
    values[jobs.path("owner", "document", "translation")] = {"job_id": job_id, "kind": "translation", "status": "running"}
    values["coordination/paid-artifact.json"] = {"operation_path": operation_path}
    runtime.store.container.list_blobs.side_effect = lambda **kwargs: documentchat_test_results([MagicMock(name=operation_path)])
    blob = MagicMock()
    blob.name = operation_path
    runtime.store.container.list_blobs.side_effect = lambda **kwargs: documentchat_test_results([blob])
    runtime.store.container.delete_blob = AsyncMock()
    if not still_active:
        runtime.store.active.side_effect = DocumentError("Dokumentet er udloebet.", 409)
    await jobs.maintain_translations()
    assert values[jobs.path("owner", "document", "translation")]["status"] == ("ready" if still_active else "cancelled")
    assert runtime.store.container.upload_blob.await_count == int(still_active)
    assert values["coordination/paid-artifact.json"] == {}
    translator.delete_staging.assert_awaited_once()
    runtime.store.container.delete_blob.assert_awaited_once_with(operation_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("reconciliation_fails", [True, False])
async def test_documentchat_unknown_translation_retains_journal_and_reservation(documentchat_artifact_runtime, reconciliation_fails):
    runtime, values = documentchat_artifact_runtime
    job_id = str(uuid.uuid4())
    operation_path = f"translation-operations/owner/document/{job_id}.json"
    operation = {"owner": "owner", "document_id": "document", "job_id": job_id,
                 "status": "submission_unknown", "created_at": datetime.now(timezone.utc).isoformat()}
    translator = MagicMock(reconcile=AsyncMock(return_value=deepcopy(operation)), poll=AsyncMock(), submit=AsyncMock(),
                           output=AsyncMock(), delete_staging=AsyncMock())
    if reconciliation_fails:
        translator.reconcile.side_effect = RuntimeError("Unsafe Translator paging link")
    jobs = ArtifactJobs(runtime, translator)
    values[operation_path] = deepcopy(operation)
    values[jobs.path("owner", "document", "translation")] = {"job_id": job_id, "kind": "translation", "status": "submission_unknown"}
    values["coordination/paid-artifact.json"] = {"operation_path": operation_path}
    blob = MagicMock()
    blob.name = operation_path
    runtime.store.container.list_blobs.side_effect = lambda **kwargs: documentchat_test_results([blob])
    runtime.store.container.delete_blob = AsyncMock()
    if reconciliation_fails:
        with pytest.raises(RuntimeError, match="paging"):
            await jobs.maintain_translations()
    else:
        await jobs.maintain_translations()
    assert values[operation_path] == operation
    assert values["coordination/paid-artifact.json"] == {"operation_path": operation_path}
    assert values[jobs.path("owner", "document", "translation")]["status"] == "submission_unknown"
    translator.poll.assert_not_awaited()
    translator.submit.assert_not_awaited()
    translator.output.assert_not_awaited()
    runtime.store.container.upload_blob.assert_not_awaited()
    runtime.store.container.delete_blob.assert_not_awaited()


@pytest.mark.asyncio
async def test_documentchat_translation_does_not_publish_when_session_ends_during_output(documentchat_artifact_runtime):
    runtime, values = documentchat_artifact_runtime
    job_id = str(uuid.uuid4())
    operation_path = f"translation-operations/owner/document/{job_id}.json"
    operation = {"owner": "owner", "document_id": "document", "job_id": job_id, "provider_id": str(uuid.uuid4()),
                 "status": "submitted", "created_at": datetime.now(timezone.utc).isoformat()}

    async def output(current):
        runtime.store.active.side_effect = DocumentError("Dokumentsessionen er afsluttet.", 409)
        return export_docx("Dansk", "Faerdigt men ikke laengere tilgaengeligt.")

    translator = MagicMock(poll=AsyncMock(return_value={**operation, "status": "terminal", "provider_status": "Succeeded"}),
                           output=AsyncMock(side_effect=output), delete_staging=AsyncMock())
    jobs = ArtifactJobs(runtime, translator)
    values[operation_path] = operation
    values[jobs.path("owner", "document", "translation")] = {"job_id": job_id, "kind": "translation", "status": "running"}
    values["coordination/paid-artifact.json"] = {"operation_path": operation_path}
    blob = MagicMock()
    blob.name = operation_path
    runtime.store.container.list_blobs.side_effect = lambda **kwargs: documentchat_test_results([blob])
    runtime.store.container.delete_blob = AsyncMock()
    await jobs.maintain_translations()
    assert values[jobs.path("owner", "document", "translation")]["status"] == "cancelled"
    runtime.store.container.upload_blob.assert_not_awaited()
    translator.delete_staging.assert_awaited_once()
    assert values["coordination/paid-artifact.json"] == {}
    runtime.store.container.delete_blob.assert_awaited_once_with(operation_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, expected",
    [
        ("Ja, jeg er her. Hvordan kan jeg hjaelpe?", []),
        ("Dokumentet giver ikke svaret.", []),
        ("Praemien er 2400 DKK [2]. Mere information [2] og [3].", [2, 3]),
        ("En ukendt kilde [99] og et link [1](https://example.com).", []),
    ],
)
async def test_documentchat_returns_only_cited_sources(answer, expected):
    owner = owner_key("tenant", "user")
    active_id = str(uuid.uuid4())
    store = MagicMock()
    store.active = AsyncMock(return_value={"status": "ready"})
    store.read = AsyncMock(return_value={"messages": []})
    store.write = AsyncMock()
    store.lock = documentchat_test_lock
    search = MagicMock()
    search.search = AsyncMock(return_value=documentchat_test_results([
        {"id": active_id + f"-{number}", "content": f"Text {number}", "page": number}
        for number in range(1, 4)
    ]))
    openai = MagicMock()
    openai.embeddings.create = AsyncMock(return_value=MagicMock(data=[MagicMock(embedding=[0.1])]))
    openai.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=answer))]))
    runtime = DocumentService(store, search, openai, MagicMock(), "chat", "embeddings")
    result = await runtime.chat(owner, active_id, "Question")
    assert [source["number"] for source in result["sources"]] == expected
    saved = store.write.call_args.args[1]["messages"][-1]
    assert saved["sources"] == result["sources"]


@pytest.mark.asyncio
async def test_documentchat_filters_sources_in_existing_history():
    sources = [{"number": number, "text": f"Text {number}"} for number in range(1, 4)]
    store = MagicMock()
    store.active = AsyncMock()
    store.read = AsyncMock(return_value={"messages": [
        {"role": "user", "content": "Er du der?"},
        {"role": "assistant", "content": "Ja, jeg er her.", "sources": sources},
        {"role": "assistant", "content": "Praemien fremgaar her [3].", "sources": sources},
    ]})
    runtime = DocumentService(store, MagicMock(), MagicMock(), MagicMock(), "chat", "embeddings")
    history = await runtime.history(owner_key("tenant", "user"), str(uuid.uuid4()))
    assert "sources" not in history[0]
    assert history[1]["sources"] == []
    assert history[2]["sources"] == [sources[2]]
    store.write.assert_not_called()


@pytest.mark.asyncio
async def test_documentchat_discards_answer_after_document_replaced():
    owner = owner_key("tenant", "user")
    active_id = str(uuid.uuid4())
    store = MagicMock()
    store.active = AsyncMock(return_value={"status": "ready"})
    store.read = AsyncMock(return_value={"messages": []})
    store.write = AsyncMock()
    store.lock = documentchat_test_lock
    search = MagicMock()
    search.search = AsyncMock(
        return_value=documentchat_test_results([{"id": active_id + "-0", "content": "Private text", "page": 1}])
    )
    openai = MagicMock()
    openai.embeddings.create = AsyncMock(return_value=MagicMock(data=[MagicMock(embedding=[0.1])]))

    async def replace_during_model_call(**kwargs):
        store.active.side_effect = DocumentError("Dokumentet er erstattet.", 409)
        return MagicMock(choices=[MagicMock(message=MagicMock(content="Old private answer"))])

    openai.chat.completions.create = AsyncMock(side_effect=replace_during_model_call)
    runtime = DocumentService(store, search, openai, MagicMock(), "chat", "embeddings")
    with pytest.raises(DocumentError, match="erstattet"):
        await runtime.chat(owner, active_id, "Question")
    store.write.assert_not_awaited()
    assert search.search.call_args.kwargs["filter"] == search_filter(owner, active_id)


@pytest.mark.asyncio
async def test_documentchat_cleanup_waits_for_search_deletion_visibility():
    owner = owner_key("tenant", "user")
    active_id = str(uuid.uuid4())
    store = MagicMock()
    store.write = AsyncMock()
    store.delete_files = AsyncMock()
    store.container.delete_blob = AsyncMock()
    search = MagicMock()
    search.search = AsyncMock(return_value=documentchat_test_results([{"id": active_id + "-0"}]))
    search.delete_documents = AsyncMock(return_value=[MagicMock(succeeded=True)])
    runtime = DocumentService(store, search, MagicMock(), MagicMock(), "chat", "embeddings")
    record = {"owner": owner, "document_id": active_id, "status": "ready"}
    await runtime.cleanup(record, MagicMock())
    assert record["status"] == "deleting"
    store.delete_files.assert_not_awaited()
    search.search.return_value = documentchat_test_results([])
    await runtime.cleanup(record, MagicMock())
    store.delete_files.assert_awaited_once_with(owner, active_id)
    store.container.delete_blob.assert_awaited_once()


@pytest.mark.asyncio
async def test_documentchat_failed_cleanup_retains_retry_record():
    store = MagicMock()
    store.write = AsyncMock()
    store.delete_files = AsyncMock()
    search = MagicMock()
    active_id = str(uuid.uuid4())
    search.search = AsyncMock(return_value=documentchat_test_results([{"id": active_id + "-0"}]))
    search.delete_documents = AsyncMock(return_value=[MagicMock(succeeded=False)])
    runtime = DocumentService(store, search, MagicMock(), MagicMock(), "chat", "embeddings")
    with pytest.raises(RuntimeError):
        await runtime.cleanup({"owner": owner_key("tenant", "user"), "document_id": active_id}, MagicMock())
    store.delete_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_documentchat_accepts_canonical_https_origin_behind_proxy(monkeypatch):
    monkeypatch.setenv("WEBSITE_HOSTNAME", "demo.azurewebsites.net")
    runtime = MagicMock()
    runtime.store.end = AsyncMock()
    owner = owner_key("tenant", "user")
    active_id = str(uuid.uuid4())
    app = create_app(service=runtime, authenticate=AsyncMock(return_value=owner))
    response = await app.test_client().post("/api/end", json={"document_id": active_id}, headers={"Origin": "https://demo.azurewebsites.net"})
    assert response.status_code == 200
    runtime.store.end.assert_awaited_once_with(owner, active_id)


@pytest.mark.asyncio
async def test_documentchat_upload_holds_document_lease_before_publishing_state():
    container = MagicMock()
    store = DocumentStore(container)
    owner, active_id = owner_key("tenant", "user"), str(uuid.uuid4())
    path = store.record_path(owner, active_id)
    held = set()
    writes = []

    @asynccontextmanager
    async def lock(blob_path, create=False):
        held.add(blob_path)
        try:
            yield MagicMock()
        finally:
            held.remove(blob_path)

    async def write(blob_path, value, lease=None):
        assert path in held
        assert blob_path in held
        assert lease is not None
        writes.append((blob_path, dict(value)))

    async def upload(*args, **kwargs):
        assert path in held

    store.lock = lock
    store.write = write
    container.upload_blob = AsyncMock(side_effect=upload)
    record = await store.replace(owner, active_id, "file.pdf", b"content", ".pdf")
    assert record["status"] == "queued"
    assert writes[0][1]["status"] == "uploading"
    assert writes[-1][1]["status"] == "queued"
    assert not held
