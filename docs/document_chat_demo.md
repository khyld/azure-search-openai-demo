# Alm. Brand Private Document Chat

## Agreed Scope

This is a separate demo, not a replacement of the existing RAG application.
It reuses the existing Entra login registrations, App Service plan, serverless
Search service, OpenAI model deployments, and Document Intelligence account.
The user explicitly approved a dedicated Storage account after inspection found
two-day blob soft delete on the original shared account.

- Danish interface and default answers; English and German documents are supported.
- One active document per signed-in user, across browser tabs.
- PDF, including scanned machine-printed text, and DOCX. Legacy DOC is not accepted.
- Upload, processing, search-readiness, error, and expired states.
- Answers cite source excerpts; PDF references use the starting page of a chunk.
  Word references use numbered text excerpts, not unstable printed page numbers.
- New Document invalidates the previous document and clears the conversation.
- End Demo and 120 minutes of inactivity make the document inaccessible and schedule deletion.
- Alm. Brand logo and observed colors are used with the user's permission.

## Deployment Layout

The translation/summary code was installed on the separate document app on
2026-10-27, but the features remain disabled. Kudu deployment
`88047980-8f7e-4653-b1ad-9d7d30f10115` completed and the new process's exact build
`3eba3924f9624215a627bbc80661d256`, readiness and login requirement were verified.
The app's managed identity passed read-only Translator/staging checks;
`budget_approved=false` and `DOCUMENTCHAT_ENABLE_ARTIFACTS=false`.
No budget ledger was created or approved, and no paid jobs were started.
An isolated regional Translator S1 and private staging
containers were provisioned and live-tested on 2026-10-27 using synthetic DOCX,
text PDF and scanned PDF inputs. Temporary test-user roles and staging files
were removed in that earlier test phase. The subsequent installation configured
`DOCUMENTCHAT_TRANSLATOR_SERVICE` and preserved all other existing settings.
The desktop-browser scope, configuration, evidence and outstanding live gates are recorded in the
[SRD implementation checkpoint](document_translation_srd.md#implementeringscheckpoint-27-oktober-2026).

| Component | Configuration |
| --- | --- |
| New website | `app-docchat-b32vfnrzdibxy`, Sweden Central |
| Website URL | `https://app-docchat-b32vfnrzdibxy.azurewebsites.net/` |
| Existing plan | `plan-b32vfnrzdibxy`, B1, Sweden Central |
| New Storage account | `stdocb32vfnrzdibxy`, Standard LRS, Sweden Central |
| Private container | `ab-documentchat` |
| Existing Search | `gptkb-sl-pl-b32vfnrzdibxy`, serverless, Poland Central |
| Separate Search index | `ab-documentchat`, 3,072-dimensional vectors |
| Existing models | `gpt-5.4-mini` and `text-embedding-3-large`, GlobalStandard |
| Existing OCR / layout | `cog-di-b32vfnrzdibxy`, West Europe |
| Infrastructure | [document-chat.bicep](../infra/document-chat.bicep) |
| Backend entry point | [documentchat_main.py](../app/backend/documentchat_main.py) |
| Frontend entry point | [documentchat.html](../app/frontend/documentchat.html) |

The GlobalStandard deployments do not guarantee Sweden-only or EU-only inference.
Document Intelligence receives uploaded content for extraction/OCR. This demo is
not an independent assessment of provider data retention, regulatory compliance,
or tenant-wide administrator access. Use approved demonstration documents.

Sharing the B1 plan shares its CPU and memory with the original app. A separate
website does not create compute isolation. The original website, index,
container, and its Storage retention configuration are not replaced.

## Why Document Limits Matter

Files are limited to **20 MiB** (`20 * 1024 * 1024` bytes). The interface uses
the familiar label "20 MB". PDF documents are limited to **100 pages**, checked
locally before the OCR call. Password-protected or unreadable PDFs are rejected.

DOCX pagination depends on Word's fonts, printer settings, and layout, so there
is no honest stable 100-page count without rendering the file. Instead, DOCX
uses a maximum of **200,000 text characters**; this is a workload limit, not a
promise that every 200,000-character document is 100 printed pages. The main
document text is checked before OCR/extraction, and extracted text is checked
again afterward. Oversized ZIP contents, excessive archive entries, and large
main-document XML are rejected. The pipeline also limits indexed chunks to 1,000.

These limits keep OCR/embedding costs, processing time, request sizes, and memory
usage bounded. They matter especially when sharing a small App Service plan and
when multiple users upload concurrently. They also reduce the risk from compressed
Word files whose expanded contents are much larger than their upload size.
Limits are enforced by the backend, not only by the browser. They do not provide
tenant-wide rate limiting or a hard spending cap.

Scanned PDF extraction uses Document Intelligence's `prebuilt-layout` model.
OCR adds latency and usage charges. Blurred, rotated, handwritten, or complex
documents may produce inaccurate or missing text. Handwriting accuracy is not
guaranteed. Images embedded inside a Word file are not guaranteed to receive OCR;
submit image-only scans as PDF. The interface and sources do not imply that an
AI-generated answer has been independently checked.

## Isolation and Document Replacement

The backend validates an Entra access token's signature, issuer, audience,
expiry, tenant, and delegated `access_as_user` scope. It derives the storage owner
key from the validated tenant/object IDs, never from a browser-supplied owner.
Easy Auth access-token headers are accepted only in the Azure-hosted app; the
application still validates the token. Local development requires a valid Bearer
token. There is no production "demo user" authentication bypass.

Every upload receives a fresh UUID. The server-side active-document manifest is
the authority. Every search filters by **both owner and active document UUID**;
download, status, history, chat, and activity also require the active manifest.
Filter values are validated, not interpolated from arbitrary browser input.
Mutating API calls require the same Origin as the website. Private API responses
use `Cache-Control: no-store`.

Selecting New Document clears the active manifest immediately, even before the
next upload. A direct replacement upload changes it atomically after validation
and storage. Existing chat history is never transferred to a new UUID. Processing
checks the manifest between external operations, and chat checks again before
returning or persisting an answer. A stale model response is rejected.

Azure Blob leases serialize per-user manifest changes and per-document work
across processes. Long work renews the lease; loss cancels the owning task.
The document state and original are stored durably, so interrupted ingestion can
be retried after a process restart. Deterministic chunk IDs prevent duplicate
chunks on retries. Ready is reported only once indexed chunks are searchable.

## Answer Citations

Search results are context for the model, not automatically evidence for its
answer. The response contains only retrieved sources explicitly referenced with
numeric citations such as `[2]` and `[3]` in the answer. Source numbers are not
renumbered, repeated citations do not duplicate the source list, and unknown
numbers do not create source entries. Markdown links such as `[1](https://...)`
are not treated as document citations, matching the frontend's citation syntax.

Greetings and other conversational replies without citations have no source
list. The model instruction also explicitly excludes citations for greetings,
ordinary conversation, and statements that the document does not provide an
answer. This is citation selection, not an independent factual-grounding check.

The same filtering applies when existing chat history is read, without a bulk
rewrite of stored conversations. Reloading the page therefore removes unrelated
source lists from old uncited answers too. The 25 document backend tests pass,
including uncited replies, source subsets, repeated/unknown citations, links,
persisted responses, and existing history.

Citation filtering was deployed as Kudu deployment
`0231ae8e-9705-4368-a86c-70c9eaad7fbc`, build
`7086c89936ed4c598827397934bd2697`. A local azd token-process timeout interrupted
monitoring after upload, but the remote build completed successfully; no second
upload was needed. The published service code was checked, only the document
web app was restarted, and exact live build/readiness/login enforcement passed.

## Expiry and Deletion

The inactivity timeout was increased from 30 to **120 minutes (two hours)** on
2026-10-26. The UI countdown and expiry notice use the same 120-minute limit.
Earlier test results dated 2026-10-24 below refer to the original 30-minute limit.
Boundary tests now cover 30 minutes, 119 minutes, 119 minutes 59 seconds, exactly
120 minutes, and 121 minutes. Existing documents already deleted are not restored.

The timeout change was deployed on 2026-10-26 as Kudu deployment
`2268ab9b-c415-4136-9d0e-1aa5030f0af6`, build
`3aa49a6d139c4a8b9f73a0c63e160093`. Only the document app was restarted;
exact live build/readiness/login enforcement passed. The 36 focused backend
and mocked-browser tests, frontend build, scoped type check, and lint passed.
Expiry boundaries were tested with simulated timestamps, not a two-hour live wait.
Document translation remains a PRD draft and is not part of this deployment.

Activity is an upload, a question, or an explicit UI activity call from typing
or opening a source. Activity calls are throttled to once per minute. Merely
leaving a tab open, polling document status, or receiving background processing
updates does **not** extend the timeout. A session already expired cannot be
revived by a later heartbeat.

The worker scans durable document records every three seconds between jobs.
It also handles cleanup; processing a large document can therefore delay the
start of other jobs and physical cleanup. The 120-minute rule is an **access
deadline**, not a guarantee of physical erasure at the exact second of expiry.
An application outage postpones cleanup until a worker is running again.
Always On is enabled, but this is a demo worker, not an independently scheduled
cleanup service with a deletion SLA.

Deletion removes indexed chunks by owner/document ID, waits for their absence
to become visible in Search, then removes original and chat blobs and finally
the work record. Partial failures retain the record for retry. No ingestion
worker can still be writing chunks when cleanup holds the document lease.
Existing in-flight model processing cannot be recalled, but its stale answer
is not returned to the user.

The dedicated Storage account disables blob soft delete, container soft delete,
and versioning. It uses HTTPS and Entra authentication, disallows anonymous blobs
and shared-key authentication, and has no immutability/backup policy provisioned
by this template. Empty or expired per-user control manifests may remain; they
contain no document text or conversation. This policy does not promise erasure
from provider operational records, browsers, downloaded originals, or source
files on the user's computer. Document content is not intentionally logged by
this application or sent to Application Insights.

## Permissions and Login

The new website has its own system-assigned managed identity:

| Role | Assignment scope | Purpose |
| --- | --- | --- |
| Storage Blob Data Contributor | Dedicated `ab-documentchat` container | Originals, job state, chat, leases, deletion |
| Search Index Data Contributor | Existing Search service | Read/write/delete chunks; service scope covers other indexes too |
| Cognitive Services OpenAI User | Existing AI account | Embeddings and answers |
| Cognitive Services User | Existing Document Intelligence account | OCR and layout extraction |

Search's built-in data role is service-scoped in this deployment; the dedicated
index and API ownership checks do not prevent a compromised managed identity
from accessing another index covered by that Azure role. Stronger infrastructure
isolation requires a separate Search service or a verified narrower authorization
mechanism. The deployment identity creates the index; the runtime identity is not
assigned Search Service Contributor or subscription Owner by this template.

The existing client app receives one additional Easy Auth web callback URI for
the new website. Existing redirects and sign-in identity bindings are preserved.
Login eligibility is the same as the original demo: this template adds no new
named-user allowlist or enterprise-app assignment requirement. Existing tenant
policies and inherited administrator rights still apply.

Easy Auth reuses the existing client credential in a secure ARM parameter and
the new app's settings. The script never prints or persists that value in a
parameter file. This demo does not create a new Key Vault; credential rotation
must account for both websites. Public network endpoints are used with identity
authorization; they are not private endpoints.

## Build and Deploy

From the repository root, build the frontend first:

```powershell
npm --prefix app/frontend run build
```

Use the existing logged-in azd identity and Python environment:

```powershell
.venv\Scripts\python.exe scripts/deploy_document_chat.py `
  --subscription e1e8db67-39be-40f1-b671-93d577ada05e `
  --resource-group rg-abg-search-demo `
  --tenant 141346e9-efbf-4062-ab81-a8819181ba24 `
  --source-app app-backend-b32vfnrzdibxy `
  --app-name app-docchat-b32vfnrzdibxy `
  --storage-name stdocb32vfnrzdibxy
```

The script compiles Bicep, validates ARM, preserves existing Entra redirects,
creates only the dedicated index if missing, and deploys incrementally. It
uploads built code through Entra-authenticated Kudu, without enabling basic
publishing credentials. It waits for actual Kudu success and checks readiness
and login enforcement. `--code-only` skips provisioning and callback/index setup.
For code updates to this existing deployment, append `--code-only --restart`.
The restart targets only the named document app after Kudu reports a completed
build. The script rejects the original source app as its deployment target,
also in code-only mode, and preserves provisioning/login configuration.
Do not redeploy the original azd backend to deploy this new site.

Each uploaded archive includes a generated `documentchat-build.json` marker,
created in memory rather than as a workspace file. The backend reads it once at
process creation and exposes the non-secret ID in `X-DocumentChat-Build`.
Deployment readiness must match that exact ID before accepting a healthy response;
an old healthy process can otherwise produce a premature deployment success.
The expected marker is printed before upload so an interrupted local monitor can
be reconciled without blindly uploading again. The azd credential subprocess
timeout is 60 seconds; a monitoring error is not proof that the remote build failed.
Append `--status-only` to the same deployment arguments for a read-only Kudu and
runtime status snapshot; it does not upload, provision or restart the app.

After configuring `DOCUMENTCHAT_TRANSLATOR_SERVICE`, the opt-in
`/healthz?artifacts=1` probe also reads staging-container properties and the
Translator job-list API using the app process's own managed identity. It returns
only sanitized access booleans and budget approval status, not provider jobs,
document metadata or credentials. It does not start translation, OCR or model
calls and does not create/approve a budget ledger. Basic `/healthz` is unchanged.
This verifies read access, not staging writes, translation billing, or all live
recovery/security gates. Artifacts remain disabled until the separate activation
requirements in the SRD are met.

For local development with real Azure services:

```powershell
$env:AZURE_STORAGE_ACCOUNT = 'stdocb32vfnrzdibxy'
$env:AZURE_DEV_USER_AGENT = 'microsoft_foundry_skill'
$env:QUART_APP = 'documentchat_main:app'
Push-Location app/backend
../../.venv/Scripts/python.exe -m quart run --port 50637
Pop-Location
```

The local API requires a valid delegated Bearer token. The deployed website is
the normal end-user demo, where Easy Auth supplies the token after login.
`/healthz` is intentionally public and returns only readiness, not document data.
Do not expose the local test/mocked browser harness as a production login bypass.

## Verification

Focused tests are in [test_documentchat.py](../tests/test_documentchat.py).
They cover expiry, replacement, owner/document filtering, malformed files,
PDF/Word workload limits, same-origin enforcement, unauthenticated requests,
server-controlled ownership, delayed answers, and cleanup retries.

[verify_document_chat.py](../scripts/verify_document_chat.py) is an explicit,
live, paid-service check using synthetic English scanned PDF and German DOCX
fixtures. It exercises OCR, Danish responses, sources, a second owner's denied
access, replacement, physical blob/index cleanup, and simulated idle expiry.
It cleans up test document data. This is backend verification, not a claim of
an authenticated browser login by the assistant.

Before presenting the demo, sign in as an ordinary user, upload a document,
ask a question, inspect a citation, replace the document, and end the session.
Repeat with a second user and confirm that the first user's document cannot be
accessed. Record the actual results separately from tests that are only planned.

### Results Recorded on 2026-10-24

- ARM validation and incremental provisioning succeeded.
- Initial Kudu application deployment `d2e7a842-f8c6-4aef-9aea-f08fb0fda829`
  completed successfully. Readiness and unauthenticated login enforcement passed.
- Final code-only deployment `93132715-0289-42f8-b9ea-d1e4eb89c099` includes the
  upload-lease and HTTPS Origin fixes. Kudu completed successfully, and readiness
  and unauthenticated login enforcement passed again.
- All 53 focused tests passed: 19 document unit/API tests, 32 existing chat
  retrieval regression tests, and 2 browser tests. Python type and lint checks
  and the production frontend build also passed.
- Chromium UI tests passed at 1366 x 900 and 390 x 844 using mocked API responses:
  upload, question, answer, source dialog, document replacement, and end session.
  Logo loading and absence of horizontal overflow were also checked.
- The live backend check passed with a synthetic scanned English PDF and a
  German DOCX, Danish model answers, and nonempty source excerpts.
- A second synthetic owner's active-document request was denied and its filtered
  Search query returned no chunks from the first owner.
- Replacing the PDF with the Word document invalidated the old document and the
  new answer used only the new sources. Old original/chat blobs and chunks were
  subsequently verified absent.
- Advancing the synthetic session timestamp past 30 minutes immediately denied
  access; cleanup then removed original/chat blobs and indexed chunks. This is
  simulated expiry, not a claim of waiting 30 real minutes in a browser.
- Live verification exposed and led to a fix for a worker/upload race. The upload
  now holds its document lease before publishing state, through activation.
- HTTPS Origin handling behind App Service's TLS proxy has a regression test.

An authenticated end-to-end login-and-chat test of the new deployed website in
the user's own browser is still pending. The assistant's browser/UI test uses
explicit API mocks; the live script uses the deployment user's service access.
Neither should be reported as an independently observed production user login.

### Browser Upload Follow-Up

The user subsequently reported that the website opened after several minutes,
but upload showed `Unexpected end of JSON input`. Platform logs recorded startup
warm-up periods of 151 and 194 seconds, followed by authentication-container
startup. Those delays are consistent with the slow initial opening; the logs
inspected did not identify the HTTP status of the user's failed upload.

The API client previously parsed every response as JSON. It now handles empty
and non-JSON responses and reports a safe HTTP-status message instead, including
specific messages for 401, 403, and 413. Server-provided JSON errors are preserved.
Empty success responses are rejected explicitly rather than passed to the UI.

The application's `Referrer-Policy` changed from `no-referrer` to `same-origin`.
This retains the same-site Referer used by Easy Auth's browser POST/CSRF checks
without sending it to other origins. Token validation, same-Origin mutation
checks, and owner/document filtering are unchanged. See Microsoft's
[Easy Auth CSRF behavior](https://learn.microsoft.com/en-us/azure/app-service/overview-authentication-authorization#mitigation-of-cross-site-request-forgery).
This addresses a concrete policy incompatibility, but is not a claim that the
user's specific failed upload was observed returning 403.

All 26 focused document backend/browser tests passed after this change, including
empty upload responses with 403, 413, 502, 503, and 200. The production frontend
build passed. A real browser upload after deployment still requires user testing.

Kudu deployment `f4763dd2-5f5f-4e5a-9ba1-9fba1ac63e47` completed, but a subsequent
live `/healthz` response still had the old `no-referrer` policy. The user also
reported the old JSON-parser error again. This revealed that the deployment
script could accept the old process's health response while new code activated.
The script now waits for the matching per-package build marker. A regression
test confirms that it skips an old healthy response; all 20 document backend
tests, Python type checks, and lint passed after this change.

Kudu deployment `4e6a52e9-ebdb-4df2-b045-d050d9b8c564` published build
`2749ca8231b541f5a984491488d4b405`. The active process still lacked its build
header until only the document web app was restarted. Afterward, live readiness
verified that exact build, `Referrer-Policy: same-origin`, and login enforcement.
The user then reloaded with Ctrl+F5 and reported that the Word upload worked.
This is a user-reported authenticated upload result, not an assistant-observed
chat/citation test or proof of the earlier HTTP 500's exact exception.
Temporary filesystem Error logging was restored to its original Off setting;
health and the matching build marker were verified again after cleanup.

## Costs and Operational Limits

The new Storage account charges for bytes and operations; constant scans of job
records also generate transactions. OCR, embeddings, chat, and Search have usage
costs. Reusing the B1 plan does not add an additional plan fee, but shared load
can require a paid scale-up. This configuration has no fixed total price claim.

Processing is sequential per worker, retries can repeat OCR/embedding charges,
and the private corpus is not antivirus approval. Production use would require
an explicit upload scanning/quarantine decision, stronger abuse/rate controls,
durable independently scheduled cleanup, deletion monitoring, budget alerts,
and a review of tenant and provider retention policies.

See the [original installation inventory](installation_abg_search_demo.md) for
the reused resources and their original security configuration.