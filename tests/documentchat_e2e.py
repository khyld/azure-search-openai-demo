import io
import json
import re
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from docx import Document
from playwright.sync_api import Page, expect


@pytest.fixture
def documentchat_url():
    directory = Path(__file__).resolve().parents[1] / "app/backend/static"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/documentchat.html"
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("viewport", [{"width": 1366, "height": 900}, {"width": 390, "height": 844}])
def test_documentchat_upload_citations_replace_and_end(page: Page, documentchat_url: str, viewport, tmp_path):
    page.set_viewport_size(viewport)
    state = {"document": None, "messages": []}
    uploads = []

    def handle(route):
        path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
        if path == "upload":
            uploads.append(len(uploads) + 1)
            state["document"] = {"document_id": str(uploads[-1]), "filename": f"police-{uploads[-1]}.pdf", "extension": ".pdf", "status": "ready", "pages": 1}
            state["messages"] = []
            response = state
        elif path == "chat":
            question = route.request.post_data_json["question"]
            premium = "2400" if len(uploads) == 1 else "3600"
            response = {"role": "assistant", "content": f"Den årlige præmie er {premium} DKK [1].", "sources": [{"number": 1, "page": 1, "chunk": 0, "text": f"Annual premium: {premium} DKK"}]}
            state["messages"].extend([{"role": "user", "content": question}, response])
        elif path == "end":
            state["document"] = None
            state["messages"] = []
            response = {"ended": True}
        elif path == "activity":
            response = {"active": True}
        else:
            response = {**state, "last_activity": datetime.now(timezone.utc).isoformat()}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(response))

    page.route("**/api/**", handle)
    page.goto(documentchat_url)
    expect(page.get_by_role("heading", name="Spørg dit dokument")).to_be_visible()
    assert page.get_by_alt_text("Alm. Brand").evaluate("image => image.complete && image.naturalWidth > 0")
    page.get_by_label("Vælg dokument").set_input_files({"name": "police.pdf", "mimeType": "application/pdf", "buffer": b"synthetic-ui-fixture"})
    expect(page.get_by_text("police-1.pdf", exact=True)).to_be_visible()
    expect(page.get_by_text("120 min. tilbage", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="Dit spørgsmål").fill("Hvad er præmien?")
    page.get_by_role("button", name="Send spørgsmål").click()
    expect(page.get_by_text("Den årlige præmie er 2400 DKK", exact=False)).to_be_visible()
    page.evaluate("document.fonts.ready")
    screenshot = tmp_path / f"documentchat-{viewport['width']}.png"
    page.screenshot(path=str(screenshot), full_page=True, animations="disabled")
    print(f"Screenshot: {screenshot}")
    page.get_by_title("Kilde 1", exact=True).click()
    expect(page.get_by_role("heading", name="Kilde 1")).to_be_visible()
    expect(page.get_by_text("Annual premium: 2400 DKK", exact=True)).to_be_visible()
    page.get_by_role("button", name="Luk kilde").click()
    with page.expect_file_chooser() as selection:
        page.get_by_role("button", name="Nyt dokument", exact=True).click()
    selection.value.set_files({"name": "replacement.pdf", "mimeType": "application/pdf", "buffer": b"synthetic-ui-fixture"})
    expect(page.get_by_text("police-2.pdf", exact=True)).to_be_visible()
    expect(page.get_by_text("Den årlige præmie er 2400 DKK", exact=False)).not_to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.get_by_role("button", name="Afslut demo og slet dokument").click()
    expect(page.get_by_role("button", name="Drop dit dokument her", exact=False)).to_be_visible()
    expect(page.get_by_role("textbox", name="Dit spørgsmål")).to_be_disabled()


@pytest.mark.parametrize("status", [403, 413, 502, 503, 200])
def test_documentchat_empty_upload_response(page: Page, documentchat_url: str, status):
    def handle(route):
        if "/api/upload" in route.request.url:
            route.fulfill(status=status, body="")
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"document": None}))

    page.route("**/api/**", handle)
    page.goto(documentchat_url)
    expect(page.get_by_role("heading", name="Spørg dit dokument")).to_be_visible()
    page.get_by_label("Vælg dokument").set_input_files(
        {"name": "police.pdf", "mimeType": "application/pdf", "buffer": b"synthetic-ui-fixture"}
    )
    expect(page.get_by_text(f"HTTP {status}", exact=False)).to_be_visible()
    expect(page.get_by_text("Unexpected end of JSON input", exact=False)).not_to_be_visible()


def test_documentchat_summary_controls_citations_and_docx(page: Page, documentchat_url: str, tmp_path):
    viewport = {"width": 1366, "height": 900}
    page.set_viewport_size(viewport)
    state = {"document": None, "jobs": [], "capabilities": {"summary": True, "translation": True}}
    uploads = []
    source = {"number": 2, "page": 100, "text": "Final important conclusion."}
    summary = {"content": "Den sidste konklusion er vigtig [2].", "sources": [source]}
    content = io.BytesIO()
    document = Document()
    document.add_paragraph(summary["content"])
    document.save(content)

    def handle(route):
        path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
        if path == "upload":
            uploads.append(route.request.post_data_buffer.decode("utf-8", errors="replace"))
            state["document"] = {"document_id": "document", "filename": "whole-document.pdf", "extension": ".pdf", "status": "ready", "pages": 100}
            state["jobs"] = [{"job_id": "summary-job", "kind": "summary", "status": "ready"}]
            response = state
        elif path.endswith("/summary"):
            response = summary
        elif path.endswith("/download"):
            route.fulfill(status=200, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", body=content.getvalue())
            return
        elif path == "activity":
            response = {"active": True}
        else:
            response = {**state, "last_activity": datetime.now(timezone.utc).isoformat()}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(response))

    page.route("**/api/**", handle)
    page.goto(documentchat_url)
    page.get_by_label("Resumé på dansk", exact=True).check()
    expect(page.get_by_label("Oversæt til dansk", exact=True)).not_to_be_checked()
    page.get_by_label("Vælg dokument").set_input_files({"name": "whole-document.pdf", "mimeType": "application/pdf", "buffer": b"synthetic-ui-fixture"})
    page.get_by_role("button", name="Vis resumé", exact=True).click()
    expect(page.get_by_label("Dokumentresumé", exact=True)).to_contain_text("Den sidste konklusion er vigtig")
    assert 'name="summary_requested"\r\n\r\ntrue' in uploads[0]
    assert 'name="translation_requested"\r\n\r\nfalse' in uploads[0]
    page.get_by_title("Kilde 2", exact=True).click()
    expect(page.get_by_text("Final important conclusion.", exact=True)).to_be_visible()
    page.get_by_role("button", name="Luk kilde", exact=True).click()
    with page.expect_download() as download:
        page.get_by_role("button", name="Download DOCX", exact=True).click()
    target = tmp_path / "resume.docx"
    download.value.save_as(target)
    assert Document(target).paragraphs[0].text == summary["content"]
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.screenshot(path=str(tmp_path / f"documentchat-summary-{viewport['width']}.png"), full_page=True, animations="disabled")


def test_documentchat_logout_always_ends_server_session(page: Page, documentchat_url: str):
    logouts = []

    def handle(route):
        if "/api/logout" in route.request.url:
            logouts.append(route.request.method)
            response = {"ended": True}
        else:
            response = {"document": None}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(response))

    page.route("**/api/**", handle)
    page.route("**/.auth/logout**", lambda route: route.fulfill(status=200, content_type="text/html", body="Logged out"))
    page.goto(documentchat_url)
    page.get_by_role("button", name="Log ud", exact=True).click()
    expect(page).to_have_url(re.compile(r"/\.auth/logout\?post_logout_redirect_uri=/$"))
    assert logouts == ["POST"]