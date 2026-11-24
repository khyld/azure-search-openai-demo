import asyncio
import io
import json
import os
import sys
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from azure.identity.aio import AzureDeveloperCliCredential
from azure.search.documents.aio import SearchClient
from azure.storage.blob.aio import ContainerClient
from dotenv_azd import load_azd_env
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/backend"))

def scanned_pdf() -> bytes:
    image = Image.new("RGB", (1800, 1800), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("app/frontend/public/documentchat/WorkSans.ttf", 54)
    lines = ["ALM. BRAND - SYNTHETIC TEST", "Policy number: DEMO-2026", "Annual premium: 2400 DKK", "Coverage begins: 1 January 2027"]
    for position, line in enumerate(lines):
        draw.text((90, 120 + position * 120), line, fill="black", font=font)
    stream = io.BytesIO()
    image.save(stream, format="PDF", resolution=120)
    return stream.getvalue()


def german_docx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>ALM. BRAND - SYNTHETISCHES TESTDOKUMENT. Die Versicherungsnummer lautet DEMO-NEU. Die Jahrespraemie betraegt 3600 DKK. Der Vertrag beginnt am 1. Februar 2028.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
    return stream.getvalue()


async def ready(runtime, owner: str, active_id: str):
    async def poll():
        while True:
            record = await runtime.store.read(runtime.store.record_path(owner, active_id))
            await runtime.run_record(record)
            record = await runtime.store.read(runtime.store.record_path(owner, active_id))
            if record.get("status") == "failed":
                raise RuntimeError("Synthetic document processing failed: " + record.get("error", ""))
            if record.get("status") == "ready":
                return
            await asyncio.sleep(2)

    await asyncio.wait_for(poll(), timeout=300)


async def deleted(runtime, owner: str, active_id: str):
    from documentchat.lifecycle import search_filter

    async def poll():
        while True:
            record = await runtime.store.read(runtime.store.record_path(owner, active_id))
            if not record:
                remaining = [blob.name async for blob in runtime.store.container.list_blobs(name_starts_with=runtime.store.prefix(owner, active_id))]
                assert not remaining, "Document blobs remain"
                result = await runtime.search.search(search_text="*", filter=search_filter(owner, active_id), include_total_count=True, top=0)
                assert await result.get_count() == 0, "Indexed chunks remain"
                return
            await runtime.run_record(record)
            await asyncio.sleep(1)

    await asyncio.wait_for(poll(), timeout=120)


async def main():
    from documentchat.lifecycle import (
        IDLE_TIMEOUT,
        DocumentError,
        owner_key,
        search_filter,
        utc_now,
        validate_upload,
    )
    from documentchat.service import DocumentService
    from documentchat.store import DocumentStore
    from prepdocslib.pdfparser import DocumentAnalysisParser
    from prepdocslib.servicesetup import OpenAIHost, setup_openai_client

    os.environ["AZURE_DEV_USER_AGENT"] = "microsoft_foundry_skill"
    load_azd_env(override=False)
    tenant = os.environ["AZURE_AUTH_TENANT_ID"]
    credential = AzureDeveloperCliCredential(tenant_id=tenant)
    container = ContainerClient("https://stdocb32vfnrzdibxy.blob.core.windows.net", "ab-documentchat", credential)
    search = SearchClient(endpoint=f"https://{os.environ['AZURE_SEARCH_SERVICE']}.search.windows.net", credential=credential, index_name="ab-documentchat")
    openai, _ = setup_openai_client(OpenAIHost.AZURE, credential, azure_openai_service=os.environ["AZURE_OPENAI_SERVICE"])
    parser = DocumentAnalysisParser(f"https://{os.environ['AZURE_DOCUMENTINTELLIGENCE_SERVICE']}.cognitiveservices.azure.com/", credential)
    runtime = DocumentService(DocumentStore(container), search, openai, parser, os.environ["AZURE_OPENAI_CHATGPT_DEPLOYMENT"], os.environ["AZURE_OPENAI_EMB_DEPLOYMENT"])
    owner = owner_key(tenant, "synthetic-test-" + str(uuid.uuid4()))
    other_owner = owner_key(tenant, "synthetic-test-" + str(uuid.uuid4()))
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    created = []
    try:
        content = scanned_pdf()
        extension = validate_upload("synthetic-scan.pdf", content)
        created.append(first_id)
        await runtime.store.replace(owner, first_id, "synthetic-scan.pdf", content, extension)
        await ready(runtime, owner, first_id)
        answer = await runtime.chat(owner, first_id, "Hvad er den aarlige praemie? Svar paa dansk.")
        assert "2400" in answer["content"].replace(".", "").replace(" ", ""), "OCR answer did not contain expected premium"
        assert answer["sources"], "OCR answer had no sources"
        print("PASS: scanned English PDF, OCR, Danish answer and sources", flush=True)

        try:
            await runtime.store.active(other_owner, first_id)
            raise AssertionError("Another owner accessed the document")
        except DocumentError:
            pass
        results = await search.search(search_text="*", filter=search_filter(other_owner, first_id), top=0, include_total_count=True)
        assert await results.get_count() == 0
        print("PASS: second owner cannot activate or retrieve the first owner's document", flush=True)

        content = german_docx()
        extension = validate_upload("synthetic-german.docx", content)
        created.append(second_id)
        await runtime.store.replace(owner, second_id, "synthetic-german.docx", content, extension)
        try:
            await runtime.store.active(owner, first_id)
            raise AssertionError("Replaced document was still active")
        except DocumentError:
            pass
        await ready(runtime, owner, second_id)
        answer = await runtime.chat(owner, second_id, "Hvad er den aarlige praemie? Svar paa dansk.")
        assert "3600" in answer["content"].replace(".", "").replace(" ", ""), "German document answer did not contain expected premium"
        assert answer["sources"] and all("2400" not in source["text"] for source in answer["sources"])
        await deleted(runtime, owner, first_id)
        print("PASS: German DOCX, document replacement, no old sources, original data deleted", flush=True)

        session_path = f"sessions/{owner}.json"
        async with runtime.store.lock(session_path) as lease:
            await runtime.store.write(session_path, {"document_id": second_id, "last_activity": (utc_now() - IDLE_TIMEOUT - timedelta(minutes=1)).isoformat()}, lease)
        try:
            await runtime.store.active(owner, second_id)
            raise AssertionError("Expired document was still accessible")
        except DocumentError:
            pass
        await deleted(runtime, owner, second_id)
        print("PASS: simulated idle expiry, original/chat blobs and indexed chunks deleted", flush=True)
        print(json.dumps({"live_backend_checks": "passed", "browser_login": "requires user verification"}), flush=True)
    finally:
        for active_id in created:
            record = await runtime.store.read(runtime.store.record_path(owner, active_id))
            if record:
                session_path = f"sessions/{owner}.json"
                async with runtime.store.lock(session_path) as lease:
                    await runtime.store.write(session_path, {}, lease)
                await deleted(runtime, owner, active_id)
        for test_owner in (owner, other_owner):
            if await runtime.store.session(test_owner):
                await runtime.store.container.delete_blob(f"sessions/{test_owner}.json")
        await search.close()
        await container.close()
        await openai.close()
        await credential.close()


if __name__ == "__main__":
    asyncio.run(main())