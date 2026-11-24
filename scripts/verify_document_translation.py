import argparse
import asyncio
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

import pymupdf
from azure.identity.aio import AzureCliCredential
from azure.storage.blob.aio import ContainerClient
from docx import Document
from openai import DefaultAsyncHttpxClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app' / 'backend'))

from documentchat.artifacts import SummaryGenerator, export_docx
from documentchat.extraction import ArtifactDocumentParser, coverage
from documentchat.lifecycle import MAX_BYTES, DocumentError, validate_upload
from documentchat.store import DocumentStore
from documentchat.translation import DocumentTranslator
from prepdocslib.servicesetup import OpenAIHost, setup_openai_client
from prepdocslib.textsplitter import SentenceTextSplitter

OWNER = 'live-verification'
CHARACTER_LIMIT = 10000
APPROVED_CHARACTER_LIMIT = 100000


class TranslationFixture(TypedDict):
    name: str
    extension: str
    language: str
    content: bytes
    characters: int
    table: bool


def fixtures() -> list[TranslationFixture]:
    english = [
        'Reference 314159. This is a synthetic test document without personal information. '
        'The delivery arrives on Monday. Customers can return an unused product within thirty days.',
        'The final section is important. Keep the receipt and contact customer service before returning the product. '
        'This example is not an insurance policy or legal advice. Final reference 271828.',
    ]
    german = [
        'Referenz 314159. Dies ist ein synthetisches Testdokument ohne personenbezogene Daten. '
        'Die Lieferung kommt am Montag. Kunden koennen ein unbenutztes Produkt innerhalb von dreissig Tagen zurueckgeben.',
        'Der letzte Abschnitt ist wichtig. Bewahren Sie den Beleg auf und kontaktieren Sie vor der Rueckgabe den Kundendienst. '
        'Dieses Beispiel ist keine Versicherungspolice oder Rechtsberatung. Letzte Referenz 271828.',
    ]
    document = Document()
    document.add_heading('Synthetic delivery document', level=1)
    document.add_paragraph(english[0])
    table = document.add_table(rows=2, cols=2)
    table.style = 'Table Grid'
    table.cell(0, 0).text = 'Product'
    table.cell(0, 1).text = 'Quantity'
    table.cell(1, 0).text = 'Notebook'
    table.cell(1, 1).text = '42'
    document.add_page_break()
    document.add_paragraph(english[1])
    buffer = io.BytesIO()
    document.save(buffer)
    cases: list[TranslationFixture] = [{'name': 'english-docx', 'extension': '.docx', 'language': 'en', 'content': buffer.getvalue(),
              'characters': len(' '.join(english)) + 100, 'table': True}]
    for name, paragraphs, scanned, language in (
        ('german-text-pdf', german, False, 'de'),
        ('english-scanned-pdf', english, True, 'en'),
    ):
        with pymupdf.open() as pdf:
            for paragraph in paragraphs:
                page = pdf.new_page()
                page.insert_textbox(pymupdf.Rect(60, 60, 520, 400), paragraph, fontsize=14)
            if scanned:
                with pymupdf.open() as raster:
                    for page in pdf:
                        image = page.get_pixmap(matrix=pymupdf.Matrix(3, 3))
                        raster.new_page(width=page.rect.width, height=page.rect.height).insert_image(
                            page.rect, stream=image.tobytes('png'))
                    if any(page.get_text().strip() for page in raster):
                        raise RuntimeError('Scanned fixture unexpectedly contains a text layer')
                    content = raster.tobytes()
            else:
                content = pdf.tobytes()
        cases.append({'name': name, 'extension': '.pdf', 'language': language, 'content': content,
                      'characters': len(' '.join(paragraphs)), 'table': False})
    if sum(case['characters'] for case in cases) > CHARACTER_LIMIT:
        raise RuntimeError('Synthetic input exceeds live-test character budget')
    return cases


def approved_cases(folder: Path, report: dict) -> list[TranslationFixture]:
    if not folder.is_dir():
        raise ValueError('Approved document folder does not exist')
    cases: list[TranslationFixture] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ('.pdf', '.docx'):
            continue
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Document links outside the approved folder are not allowed')
        item = {'case': path.stem, 'filename': path.name, 'bytes': path.stat().st_size}
        if item['bytes'] > MAX_BYTES:
            item.update(status='blocked_upload', reason='size_limit', limit_bytes=MAX_BYTES)
            report['cases'].append(item)
            continue
        content = path.read_bytes()
        try:
            extension = validate_upload(path.name, content)
        except DocumentError:
            item.update(status='blocked_upload', reason='upload_validation')
            report['cases'].append(item)
            continue
        if extension == '.docx':
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if any(name.startswith('word/media/') for name in archive.namelist()):
                    item.update(status='blocked_artifacts', reason='embedded_images_not_verified',
                                translation_status='not_needed_danish', summary_status='blocked')
                    report['cases'].append(item)
                    continue
        cases.append({'name': path.stem, 'extension': extension, 'language': 'da' if extension == '.docx' else 'en',
                      'content': content, 'characters': 0, 'table': False})
    if len(cases) > 6:
        raise ValueError('Approved live verification is limited to six processable files')
    return cases


async def wait_terminal(translator: DocumentTranslator, operation: dict, cancel: bool = False) -> dict:
    deadline = time.monotonic() + 240
    while operation.get('status') != 'terminal':
        if time.monotonic() > deadline:
            raise TimeoutError('Provider not terminal; preserve operation metadata and resume, never resubmit')
        operation = await translator.poll(operation, cancel=cancel)
        cancel = False
        if operation.get('status') != 'terminal':
            await asyncio.sleep(2)
    return operation


async def clean_terminal(translator: DocumentTranslator, operation: dict) -> bool:
    if operation.get('status') == 'submission_unknown':
        operation = await translator.reconcile(operation)
    if operation.get('provider_id') and operation.get('status') != 'terminal':
        operation = await wait_terminal(translator, operation, cancel=True)
    if operation.get('status') not in ('terminal', 'prepared'):
        print('UNRESOLVED: preserve operation and resume with --resume-operation ' +
              translator.operation_path(operation['owner'], operation['document_id'], operation['job_id']), flush=True)
        return False
    await translator.delete_staging(operation)
    for container in (translator.source, translator.target):
        if any([blob async for blob in container.list_blobs(name_starts_with=operation['blob_prefix'])]):
            raise RuntimeError('Staging blobs remain after cleanup')
    await translator.source.delete_blob(translator.operation_path(operation['owner'], operation['document_id'], operation['job_id']))
    return True


def validate_output(content: bytes, table_required: bool, synthetic: bool = True) -> str:
    document = Document(io.BytesIO(content))
    text = '\n'.join([paragraph.text for paragraph in document.paragraphs] +
                     [cell.text for table in document.tables for row in table.rows for cell in row.cells])
    if synthetic and not all(marker in text for marker in ('314159', '271828')):
        raise RuntimeError('Translation is missing beginning or final-section reference')
    if table_required and (not document.tables or '42' not in text):
        raise RuntimeError('Simple table or its value was lost')
    if len(text.strip()) < 100:
        raise RuntimeError('Translation contains too little readable text')
    return text


async def verify(args, cases: list[TranslationFixture], report: dict) -> None:
    async with AzureCliCredential(tenant_id=args.tenant, process_timeout=60) as credential:
        source = ContainerClient(f'https://{args.storage}.blob.core.windows.net',
                                 'ab-documentchat-translation-source', credential)
        target = ContainerClient(f'https://{args.storage}.blob.core.windows.net',
                                 'ab-documentchat-translation-target', credential)
        openai = None
        if args.input_folder:
            openai, _ = setup_openai_client(OpenAIHost.AZURE, credential, azure_openai_service=args.openai_service)
        translator = DocumentTranslator(args.service, credential, source, target, DocumentStore(source), openai, args.model)
        try:
            response = await translator.request('GET', '')
            report['entra_http_status'] = response.status_code
            response.raise_for_status()
            print('PASS: Entra-only Translator GET, HTTP ' + str(response.status_code), flush=True)
            if args.resume_operation:
                if not re.fullmatch(r'translation-operations/live-verification/[0-9a-f-]{36}/[0-9a-f-]{36}\.json', args.resume_operation):
                    raise ValueError('Resume is restricted to this script\'s synthetic operations')
                operation = await translator.store.read(args.resume_operation)
                if not operation:
                    raise RuntimeError('Resume operation not found')
                report['resumed_cleanup'] = await clean_terminal(translator, operation)
                if not report['resumed_cleanup']:
                    raise RuntimeError('Provider submission still unresolved; no new submission made')
                return
            pending = [blob.name async for blob in source.list_blobs(name_starts_with=f'translation-operations/{OWNER}/')]
            if pending:
                raise RuntimeError('Prior operation requires --resume-operation ' + pending[0])
            parser = ArtifactDocumentParser(f'https://{args.document_intelligence}.cognitiveservices.azure.com', credential)
            extracted_characters = 0
            for case in cases:
                print('START: ' + case['name'], flush=True)
                pages = [page async for page in parser.parse(io.BytesIO(case['content']))]
                quality = coverage(case['content'], case['extension'], pages)
                item = {'case': case['name'], 'coverage': quality, 'pages': len(pages)}
                report['cases'].append(item)
                if quality['status'] != 'adequate':
                    if args.input_folder:
                        item.update(status='blocked_coverage', translation_status='blocked', summary_status='blocked')
                        print('BLOCKED: ' + case['name'] + ' (coverage)', flush=True)
                        continue
                    raise RuntimeError('Extraction rejected before Translator POST: ' + json.dumps(item))
                extracted_characters += sum(len(page.text) for page in pages) + 50
                if extracted_characters > report['character_limit']:
                    raise RuntimeError('Extracted content exceeds test budget; no additional paid calls permitted')
                chunks = list(SentenceTextSplitter().split_pages(pages))
                extraction = {'pages': [{'page': page.page_num + 1, 'text': page.text} for page in pages],
                              'sources': [{'number': number, 'page': chunk.page_num + 1, 'text': chunk.text}
                                          for number, chunk in enumerate(chunks, start=1)]}
                (args.output / (case['name'] + '-extraction.json')).write_text(json.dumps(extraction, indent=2), encoding='utf-8')
                item['source_chunks'] = len(chunks)
                if openai is not None:
                    async def checkpoint():
                        return None

                    try:
                        summary = await asyncio.wait_for(SummaryGenerator(openai, args.model).generate(extraction, checkpoint), timeout=600)
                        summary_docx = export_docx('Resume', summary['content'], summary['sources'], pdf=case['extension'] == '.pdf')
                        (args.output / (case['name'] + '-resume.json')).write_text(json.dumps(summary, indent=2), encoding='utf-8')
                        (args.output / (case['name'] + '-resume.md')).write_text(summary['content'], encoding='utf-8')
                        (args.output / (case['name'] + '-resume.docx')).write_bytes(summary_docx)
                        item.update(summary_status='ready_for_review', summary_words=len(summary['content'].split()),
                                    summary_usage=summary['usage'], summary_calls=summary['model_calls'],
                                    cited_pages=sorted({source['page'] for source in summary['sources']}))
                        print('SUMMARY READY: ' + case['name'], flush=True)
                    except Exception as error:
                        item.update(summary_status='failed', summary_error_type=type(error).__name__)
                        print('SUMMARY FAILED: ' + case['name'] + ' (' + type(error).__name__ + ')', flush=True)
                    if case['language'] == 'da':
                        item['translation_status'] = 'not_needed_danish'
                        continue
                try:
                    staged = export_docx('Dokument', '\n\n'.join(page.text for page in pages))
                    validate_output(staged, case['table'], synthetic=not args.input_folder)
                    item['staged_docx_verified'] = True
                except DocumentError:
                    if not args.input_folder:
                        raise
                    item['translation_status'] = 'blocked_docx_conversion'
                    continue
                job = {'job_id': str(uuid4()), 'source_language': case['language']}
                operation = None
                try:
                    operation = await translator.stage(OWNER, str(uuid4()), job, extraction)
                    operation = await translator.submit(operation)
                    item['provider_id'] = operation.get('provider_id')
                    if not operation.get('provider_id'):
                        raise RuntimeError('Submission not confirmed: ' + operation['status'])
                    operation = await wait_terminal(translator, operation)
                    item['provider_status'] = operation.get('provider_status')
                    item['provider_summary'] = operation.get('summary')
                    content = await translator.output(operation)
                    text = validate_output(content, case['table'], synthetic=not args.input_folder)
                    if len(Document(io.BytesIO(content)).tables) != len(Document(io.BytesIO(staged)).tables):
                        raise RuntimeError('Translation changed the number of reconstructed tables')
                    (args.output / (case['name'] + '-da.docx')).write_bytes(content)
                    item.update(output_bytes=len(content), readable_docx=True, translation_status='ready_for_review',
                                source_characters=sum(len(page.text) for page in pages))
                    if args.input_folder:
                        print('TRANSLATION READY: ' + case['name'], flush=True)
                    else:
                        item['beginning_and_end'] = True
                        print('PASS: ' + case['name'] + '\n' + text, flush=True)
                finally:
                    if operation:
                        item['staging_deleted'] = await clean_terminal(translator, operation)
                if not item.get('staging_deleted'):
                    raise RuntimeError('Cleanup incomplete; do not start another paid operation')
            report['extracted_characters'] = extracted_characters
            charged_characters = sum(item.get('provider_summary', {}).get('totalCharacterCharged', 0) for item in report['cases'])
            report['provider_charged_characters'] = charged_characters
            report['translator_list_usd_estimate'] = round(charged_characters * 15 / 1000000, 6)
        finally:
            await translator.close()
            if openai is not None:
                await openai.close()


async def verify_saved_summary(args, report: dict) -> None:
    extraction = json.loads(args.summary_from.read_text(encoding='utf-8'))
    if not extraction.get('sources') or sum(len(source['text']) + 40 for source in extraction['sources']) > APPROVED_CHARACTER_LIMIT:
        raise ValueError('Saved extraction is empty or exceeds the approved summary budget')
    name = args.summary_from.name.removesuffix('-extraction.json')
    item: dict = {'case': name, 'extraction_reused': True, 'summary_status': 'running'}
    report['cases'].append(item)
    async def audit_response(response):
        await response.aread()
        data = response.json()
        for choice in data.get('choices', []):
            text = choice.get('message', {}).get('content') or ''
            item.setdefault('model_responses', []).append({
                'finish_reason': choice.get('finish_reason'), 'content_characters': len(text),
                'numeric_citations': sorted(set(re.findall(r'\[(\d+)\](?!\()', text))),
                'usage': data.get('usage'),
            })

    async with AzureCliCredential(tenant_id=args.tenant) as credential:
        base_client, _ = setup_openai_client(OpenAIHost.AZURE, credential, azure_openai_service=args.openai_service)
        openai = base_client.with_options(http_client=DefaultAsyncHttpxClient(event_hooks={'response': [audit_response]}))
        try:
            async def checkpoint():
                return None

            summary = await asyncio.wait_for(SummaryGenerator(openai, args.model).generate(extraction, checkpoint), timeout=600)
            content = export_docx('Resume', summary['content'], summary['sources'], pdf=True)
            (args.output / (name + '-resume.json')).write_text(json.dumps(summary, indent=2), encoding='utf-8')
            (args.output / (name + '-resume.md')).write_text(summary['content'], encoding='utf-8')
            (args.output / (name + '-resume.docx')).write_bytes(content)
            item.update(summary_status='ready_for_review', summary_words=len(summary['content'].split()),
                        summary_usage=summary['usage'], summary_calls=summary['model_calls'],
                        cited_pages=sorted({source['page'] for source in summary['sources']}))
        except DocumentError as error:
            item.update(summary_status='failed', summary_error_type=type(error).__name__, summary_error_message=str(error))
        finally:
            await openai.close()
            await base_client.close()
    print('SUMMARY: ' + item['summary_status'] + ' ' + item.get('summary_error_message', ''), flush=True)


def verify_app_browser(args, report: dict) -> None:
    from playwright.sync_api import expect, sync_playwright

    base = 'https://app-docchat-b32vfnrzdibxy.azurewebsites.net'
    expected_user = 'd4b92084-5272-47eb-a4e7-65ebeaaa6c42'
    case = next(case for case in fixtures() if case['name'] == 'english-scanned-pdf')
    report['cases'].append({'case': case['name'], 'via_live_app': True})
    item = report['cases'][-1]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True, viewport={'width': 1366, 'height': 900})
        page = context.new_page()
        created_document = None

        def read(path: str):
            response = context.request.get(base + path, max_redirects=0)
            if not response.ok:
                raise RuntimeError(f'Live app GET failed: HTTP {response.status}')
            return response.json()

        try:
            print('BROWSER LOGIN REQUIRED: Sign in normally in the opened Chromium window. Never paste passwords or tokens into chat.', flush=True)
            page.goto(base, wait_until='domcontentloaded', timeout=120000)
            page.wait_for_url(base + '/', timeout=600000)
            page.wait_for_function("async () => { const response = await fetch('/api/session'); return response.ok; }", timeout=600000)
            identities = read('/.auth/me')
            claims = {claim['typ']: claim['val'] for identity in identities for claim in identity.get('user_claims', [])}
            user = claims.get('oid') or claims.get('http://schemas.microsoft.com/identity/claims/objectidentifier')
            tenant = claims.get('tid') or claims.get('http://schemas.microsoft.com/identity/claims/tenantid')
            if user != expected_user or tenant != args.tenant:
                raise RuntimeError('Live test requires the approved demo user and tenant')
            session = read('/api/session')
            if session.get('document'):
                raise RuntimeError('An active document exists. End it manually before running this synthetic test; nothing was replaced.')
            if session.get('capabilities') != {'summary': True, 'translation': True}:
                raise RuntimeError('Live artifact capabilities are not enabled')
            summary_option = page.get_by_label('Resumé på dansk', exact=True)
            translation_option = page.get_by_label('Oversæt til dansk', exact=True)
            expect(summary_option).not_to_be_checked()
            expect(translation_option).not_to_be_checked()
            summary_option.check()
            translation_option.check()
            page.get_by_label('Kildesprog', exact=True).select_option('en')
            with page.expect_response(lambda response: response.url == base + '/api/upload', timeout=120000) as upload:
                page.get_by_label('Vælg dokument', exact=True).set_input_files({
                    'name': case['name'] + '.pdf', 'mimeType': 'application/pdf', 'buffer': case['content'],
                })
            if upload.value.status != 202:
                raise RuntimeError(f'Live upload failed: HTTP {upload.value.status}')
            created_document = upload.value.json()['document']['document_id']
            item['document_id'] = created_document
            print('LIVE: authenticated synthetic upload accepted; waiting for whole-document jobs', flush=True)
            page.wait_for_function("""async id => {
                const response = await fetch('/api/document/' + id + '/jobs');
                if (!response.ok) throw new Error('Live job lookup failed');
                const jobs = (await response.json()).jobs;
                if (jobs.some(job => ['failed', 'submission_unknown', 'cancelled'].includes(job.status)))
                    throw new Error('A live artifact job failed; no automatic retry');
                return jobs.length === 2 && jobs.every(job => job.status === 'ready');
            }""", arg=created_document, timeout=600000, polling=2000)
            jobs = read(f'/api/document/{created_document}/jobs')['jobs']
            for job in jobs:
                region = page.get_by_role('region', name='Resumé' if job['kind'] == 'summary' else 'Oversættelse', exact=True)
                with page.expect_download(timeout=60000) as downloaded:
                    region.get_by_role('button', name='Download DOCX', exact=True).click()
                output = args.output / (case['name'] + '-' + job['kind'] + '.docx')
                downloaded.value.save_as(output)
                if job['kind'] == 'translation':
                    validate_output(output.read_bytes(), table_required=False)
                else:
                    result = read(f'/api/document/{created_document}/jobs/{job["job_id"]}/summary')
                    exported = '\n'.join(paragraph.text for paragraph in Document(output).paragraphs)
                    if re.sub(r'\s+', ' ', result['content']) not in re.sub(r'\s+', ' ', exported):
                        raise RuntimeError('Downloaded summary differs from canonical summary')
                    if {source['page'] for source in result['sources']} != {1, 2}:
                        raise RuntimeError('Summary does not cite both original pages')
                    region.get_by_role('button', name='Vis resumé', exact=True).click()
                    item['summary_words'] = len(result['content'].split())
                item[job['kind'] + '_status'] = 'ready_verified'
            page.screenshot(path=str(args.output / 'live-app.png'), full_page=True)
            item['status'] = 'passed'
            print('LIVE: both real DOCX downloads verified; original-page citations verified', flush=True)
        finally:
            if created_document is not None:
                logout = context.request.post(base + '/api/logout', headers={'Origin': base}, data={})
                if not logout.ok or read('/api/session').get('document') is not None:
                    raise RuntimeError('Server-owned logout did not close the live test session')
                old_jobs = context.request.get(base + f'/api/document/{created_document}/jobs', max_redirects=0)
                if old_jobs.status not in (404, 409):
                    raise RuntimeError('Ended document is still accessible')
                item['server_logout_verified'] = True
                print('LIVE: server logout closed session; ended document is inaccessible', flush=True)
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Bounded synthetic or explicitly approved live verification; never enables the app.')
    parser.add_argument('--service', required=True)
    parser.add_argument('--storage', required=True)
    parser.add_argument('--document-intelligence', required=True)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume-operation')
    parser.add_argument('--input-folder', type=Path)
    parser.add_argument('--approved-documents', action='store_true')
    parser.add_argument('--openai-service')
    parser.add_argument('--model', default='gpt-5.4-mini')
    parser.add_argument('--summary-from', type=Path)
    parser.add_argument('--app-browser', action='store_true')
    args = parser.parse_args()
    if args.app_browser and (args.input_folder or args.prepare_only or args.resume_operation or args.summary_from):
        parser.error('Interactive live-app verification must run separately from direct provider tests')
    if args.summary_from and (not args.input_folder or args.prepare_only or args.resume_operation or
                              not args.summary_from.name.endswith('-extraction.json')):
        parser.error('Saved-summary verification requires approved input, an extraction JSON, and no other run mode')
    if args.input_folder and (not args.approved_documents or not args.openai_service):
        parser.error('Real input requires --approved-documents and --openai-service; DOCX language is da, PDF language en')
    if args.input_folder and (args.output.resolve().is_relative_to(args.input_folder.resolve()) or
                              args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1])):
        parser.error('Real results must be outside both the input folder and repository')
    if not all(re.fullmatch(r'[a-zA-Z0-9-]{3,63}', value) for value in (args.service, args.storage, args.document_intelligence)):
        parser.error('Invalid Azure service name')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output directory must be new or empty; do not overwrite prior evidence')
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'synthetic_only': not args.input_folder, 'character_limit': APPROVED_CHARACTER_LIMIT if args.input_folder else CHARACTER_LIMIT,
              'cases': [], 'service': args.service,
              'prepare_only': args.prepare_only, 'completed': False}
    try:
        if args.app_browser:
            verify_app_browser(args, report)
        elif args.summary_from:
            asyncio.run(verify_saved_summary(args, report))
        else:
            cases = approved_cases(args.input_folder, report) if args.input_folder else fixtures()
            if not args.input_folder:
                for case in cases:
                    (args.output / (case['name'] + case['extension'])).write_bytes(case['content'])
            if not args.prepare_only:
                asyncio.run(verify(args, cases, report))
        report['completed'] = True
    finally:
        (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('Evidence: ' + str(args.output.resolve()), flush=True)


if __name__ == '__main__':
    main()