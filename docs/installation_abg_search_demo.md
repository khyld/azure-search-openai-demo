# Installed Environment: abg-search-demo

Original snapshot verified against Azure on **2026-10-24**, with a dated Translator
addition below from **2026-10-27**. This is an environment-specific snapshot,
not the default architecture of every deployment of this repository.

The original resource inventory and Azure RBAC were read without changes.
The dated Translator addition records subsequent provisioning and verification.
IDs and endpoint names below are identifiers, not credentials.
Do not add secrets, access tokens, account keys, or the contents of the local
azd environment file to this document.

## Environment

| Setting | Value |
| --- | --- |
| azd environment | `abg-search-demo` |
| Subscription | `ME-MngEnvMCAP034994-khyld-1` |
| Subscription ID | `e1e8db67-39be-40f1-b671-93d577ada05e` |
| Entra tenant ID | `141346e9-efbf-4062-ab81-a8819181ba24` |
| Resource group | `rg-abg-search-demo` |
| Primary deployment region | Sweden Central (`swedencentral`) |
| Hosting | Linux Azure App Service |
| Application | <https://app-backend-b32vfnrzdibxy.azurewebsites.net/> |

## Installed Resources and Regions

The original demo snapshot below contains 12 ARM resources. Later on 2026-10-24,
the separate [private document chat demo](document_chat_demo.md) added its own web
application and Storage account in the same resource group. Those additions and
their managed-identity roles are documented separately; this table is no longer
an exhaustive current inventory of the resource group. Model deployments and
storage containers are described separately from top-level services.

| Component | Azure resource name | Region | SKU or purpose |
| --- | --- | --- | --- |
| App Service plan | `plan-b32vfnrzdibxy` | Sweden Central | Basic B1; one instance |
| Web application | `app-backend-b32vfnrzdibxy` | Sweden Central | Python backend and built React frontend |
| Foundry / AI Services account | `cog-b32vfnrzdibxy` | Sweden Central | `AIServices`, S0; hosts model deployments |
| Foundry project | `cog-b32vfnrzdibxy/proj-b32vfnrzdibxy` | Sweden Central | Project under the AI Services account |
| Azure AI Search | `gptkb-sl-pl-b32vfnrzdibxy` | Poland Central | **Serverless**, not Basic or a dedicated replica-based tier |
| Storage account | `stb32vfnrzdibxy` | Sweden Central | StorageV2, Standard LRS |
| Document Intelligence | `cog-di-b32vfnrzdibxy` | West Europe | FormRecognizer, S0 |
| Storage Event Grid system topic | `stb32vfnrzdibxy-4a25e226-9311-4cb0-9176-119dda86b884` | Sweden Central | Existing storage-related system topic |
| Active Search user-assigned identity | `gptkb-sl-pl-b32vfnrzdibxy-identity` | Poland Central | Attached to the active Search service |
| Earlier Search identity | `gptkb-b32vfnrzdibxy-identity` | Sweden Central | Remnant of an earlier provisioning attempt |
| Earlier serverless Search identity | `gptkb-sl-b32vfnrzdibxy-identity` | Sweden Central | Remnant of an earlier provisioning attempt |
| Earlier Italy Search identity | `gptkb-sl-it-b32vfnrzdibxy-identity` | Italy North | Remnant of the failed Italy North attempt |

Only the Poland Central Search service exists as a Search service in this
resource group. The older identities do not indicate additional running Search
services. They have not been deleted; review references before any cleanup.
The Event Grid topic's presence alone does not establish that an ingestion
subscription or an Azure Function is active.

The App Service plan is a paid B1 plan. Search is serverless, but the complete
installation is not free. Model consumption, storage, Search, and any use of
Document Intelligence can also incur charges. No fixed monthly estimate is
asserted here.

## Document Translation Addition: 2026-10-27

- `cog-trdoc-b32vfnrzdibxy`: single-service `TextTranslation`, regional Sweden
	Central, S1 pay-as-you-go, system-assigned identity, local key auth disabled.
- Document endpoint: `https://cog-trdoc-b32vfnrzdibxy.cognitiveservices.azure.com`.
	ARM's generic Translator endpoint is not the Document Translation endpoint.
- Deployment: `almbrand-documentchat-translator`, succeeded. Reproducible template:
	[infra/document-chat-translator.bicep](../infra/document-chat-translator.bicep).
- Private containers in `stdocb32vfnrzdibxy`:
	`ab-documentchat-translation-source` and `ab-documentchat-translation-target`.
	No original Storage retention, versioning, network or identity settings changed.
- Translator principal `afaf35e2-7c23-4c86-9c5a-8e26d4453b77` has Blob Data
	Contributor on those two containers only. Document app principal
	`858ee523-e46c-4237-b605-dbde114f0eb6` has the same staging roles and Cognitive
	Services User on the Translator. Three temporary test-user roles were removed.
- Entra-only live checks translated synthetic English DOCX, German text PDF and
	English scanned PDF into genuine Danish DOCX. Final results and original
	staging were deleted from Azure after download; both containers were empty.
- Provider usage across attempts: 2,112 charged characters; S1 Translator-only
	list estimate USD 0.03168, plus OCR and Storage. This is not an invoice or
	proof of the complete application's monthly cost.
- No app deployment, app settings changes or feature activation occurred during
	that synthetic test phase. The later application installation is recorded below.
	See [live evidence and remaining gates](document_translation_srd.md#live-verifikation-27-oktober-2026).

### Document App Code Installation: 2026-10-27

- User confirmed successful login and authorized continuing installation.
- Only `app-docchat-b32vfnrzdibxy` was updated, code-only, then restarted.
	Kudu `88047980-8f7e-4653-b1ad-9d7d30f10115` succeeded; exact active build
	`3eba3924f9624215a627bbc80661d256`, HTTP 200 readiness and required login verified.
- `DOCUMENTCHAT_TRANSLATOR_SERVICE=cog-trdoc-b32vfnrzdibxy`;
	`DOCUMENTCHAT_ENABLE_ARTIFACTS=false`. All other existing app settings were
	compared and preserved. No new resource, role, identity or login changes.
- Runtime managed identity read probe: Translator and staging properties passed;
	budget approval false. This does not prove staging writes or a complete live job.
- No budget approval/reset or paid test/job in this installation phase. Features
	remain inactive; [activation gates and evidence](document_translation_srd.md#appinstallation-27-oktober-2026)
	are separate from successful code installation.
- Original app, Search index, Storage account and retention were not modified.

## Models, Runtime, and Data

### Model Deployments

Both deployments are under `cog-b32vfnrzdibxy`; both reported `Succeeded`.

| Purpose | Deployment / model | Model version | Deployment SKU | ARM capacity |
| --- | --- | --- | --- | --- |
| Chat and query rewriting | `gpt-5.4-mini` | `2026-03-17` | `GlobalStandard` | 30 |
| Text embeddings | `text-embedding-3-large` | `1` | `GlobalStandard` | 200 |

Capacity is the configured ARM SKU capacity, not a measured requests-per-second
rate. Both use content filter policy `Microsoft.DefaultV2` and version upgrade
option `OnceNewDefaultVersionAvailable`. Model versions can therefore change
after this snapshot when a new default becomes available.

**Data residency:** the AI account is in Sweden Central, but these are
**GlobalStandard** deployments. The account location is not a guarantee that
inference stays in Sweden or the EU. Review the service's current Global
deployment data-processing terms before imposing a residency requirement.
Search is separately located in Poland Central, so application-to-Search traffic
also crosses regions. Document Intelligence is in West Europe when used.

### Application Runtime

- App Service runtime target: `python|3.11`, Linux.
- Startup command: `python3 -m gunicorn main:app`; the repository configures the
	ASGI worker used by the Quart application.
- Frontend: React, TypeScript, and Vite. The built frontend is served by the
	backend, not a separate Static Web App.
- Always On: enabled. The App Service plan has one B1 instance.
- Python dependency pins: [backend requirements](../app/backend/requirements.txt).
- Frontend dependency declarations and exact lockfile:
	[package.json](../app/frontend/package.json) and
	[package-lock.json](../app/frontend/package-lock.json).

The manifests document the source dependency set; this inventory is not a
package-by-package software bill of materials extracted from the running worker.

### Search and Storage

| Item | Verified state |
| --- | --- |
| Search endpoint | `https://gptkb-sl-pl-b32vfnrzdibxy.search.windows.net` |
| Index | `gptkbindex` |
| Indexed documents / chunks | 752 at inspection time; this is not a count of original files |
| Vector field | `embedding3`, 3,072 dimensions, profile `embedding3-profile` |
| Configured Search vectorizer | `text-embedding-3-large-vectorizer`, kind `azureOpenAI` |
| Semantic ranker | Disabled in both Search service configuration and app settings |
| Storage blob endpoint | `https://stb32vfnrzdibxy.blob.core.windows.net` |
| `content` container | Present; public access `None`; original documents |
| `images` container | Present; public access `None`; reserved for image content |
| `tokens` container | Present; public access `None`; not evidence of active Blob-backed token storage |

Easy Auth's token store is enabled, but its Azure Blob token-store properties
`sasUrlSettingName`, `blobContainerUri`, and `clientId` were all unset. Therefore,
do not treat the `tokens` container as the active App Service token store on the
basis of its name. The configured Search vectorizer similarly does not mean
cloud ingestion is active: local ingestion generated and uploaded embeddings.

### Ingestion and Optional Features

Documents were loaded with the local `app/backend/prepdocs.py` pipeline, using
the deployment user's Azure Developer CLI identity. The local azd configuration
has `USE_LOCAL_PDF_PARSER=true` and `USE_LOCAL_HTML_PARSER=true`. These are local
ingestion settings, not claimed to be runtime App Service settings. Document
Intelligence is provisioned, but the verified PDF ingestion used the local
parser rather than that service.

The deployed app settings show user upload, Azure speech output, browser chat
history, and Cosmos DB chat history disabled. No Cosmos DB account, Azure
Functions, Container Apps, Key Vault, Application Insights, or Log Analytics
workspace appears in this resource group's inventory. The local azd setting
`AZURE_USE_APPLICATION_INSIGHTS=false` is consistent with that inventory; an
external tenant-wide telemetry configuration was not audited.

The Event Grid system topic has an event subscription named
`StorageAntimalwareSubscription`, with WebHook delivery for
`Microsoft.Storage.BlobCreated` and `Microsoft.Storage.BlobRenamed`.
This is the observed storage-antimalware subscription, not an Azure AI Search
document-ingestion indexer. Delivery and malware-detection outcomes were not
tested as part of this documentation task.

## Service Identities

The principal IDs below identify objects in the deployment tenant. A managed
identity is a service identity, not a human login or an application secret.

| Identity | Principal / object ID | Usage |
| --- | --- | --- |
| Web app system-assigned identity | `d6672c17-89e1-4bce-8bd5-5629e7fa48cd` | Runtime access to Search, storage, and AI services |
| Search system-assigned identity | `8f7593c9-c10f-4c7b-8fa0-56c6632ddfba` | Search-side access to OpenAI |
| Search user-assigned identity, Poland | `34c7853c-831c-4411-91b2-413b460f28d8` | Attached identity; no direct RBAC assignment found within this resource group |
| Foundry project system-assigned identity | `f6946913-6180-41b0-a25c-098e30fdfada` | Project identity; no direct RBAC assignment found within this resource group |
| Deployment / ingestion user, Ken Hyld | `d4b92084-5272-47eb-a4e7-65ebeaaa6c42` | `khyld@microsoft.com`, represented by an external tenant account |

The active Search user-assigned identity has client ID
`11e7e3ef-042e-4f5d-9d12-5a4bfc1fbbbe`. A client ID and a principal ID are not
interchangeable: role assignments refer to the principal/object ID.

## Actual Azure RBAC Assignments

`RG` below means `/subscriptions/e1e8db67-39be-40f1-b671-93d577ada05e/resourceGroups/rg-abg-search-demo`.
`Project` means the `proj-b32vfnrzdibxy` child resource under `cog-b32vfnrzdibxy`.
The matrix lists actual assignments, rather than every conditional role present
in the Bicep source. The resource-group assignments also apply to applicable
child resources, not just the currently used instance of a service.

### Application and Search

| Principal | Scope | Assigned role | What it permits |
| --- | --- | --- | --- |
| Web app managed identity | RG | `Search Index Data Reader` | Query index documents; not modify index content |
| Web app managed identity | RG | `Storage Blob Data Reader` | Read document blobs for application content and citations |
| Web app managed identity | RG | `Cognitive Services OpenAI User` | Invoke OpenAI models, including chat and embeddings |
| Web app managed identity | RG | `Cognitive Services Speech User` | Use Speech APIs if enabled; assignment does not imply the feature is active |
| Web app managed identity | RG | `Reader` | Read Azure resource configuration; not write resources or assign roles |
| Web app managed identity | Project | `Foundry User` | Use the Foundry project under that role's built-in permissions |
| Search system-assigned identity | RG | `Cognitive Services OpenAI User` | Invoke OpenAI from Search, for example through a configured vectorizer |

No direct storage write, Search data write, or resource administration assignment
was found for the web app identity within this resource group. The Search
user-assigned identity is attached to the service, but the OpenAI assignment
above belongs to its **system-assigned** identity. Do not conflate them.

### Deployment and Local Ingestion User

| Principal | Scope | Assigned roles | What they permit |
| --- | --- | --- | --- |
| Ken Hyld | RG | `Search Service Contributor` | Manage Search service/index configuration; distinct from document data access |
| Ken Hyld | RG | `Search Index Data Contributor`, `Search Index Data Reader` | Write and query indexed document content |
| Ken Hyld | RG | `Storage Blob Data Contributor`, `Storage Blob Data Reader` | Upload, update, delete, and read document blobs |
| Ken Hyld | RG | `Cognitive Services OpenAI User` | Generate embeddings and invoke chat models |
| Ken Hyld | RG | `Cognitive Services User` | Use Cognitive Services, including document processing when configured |
| Ken Hyld | RG | `Cognitive Services Speech User` | Use Speech APIs when configured |
| Ken Hyld | Project | `Foundry Project Manager` | Manage the Foundry project under that role's built-in permissions |

These data-plane roles explain why local ingestion can upload blobs, create or
update the index, and generate embeddings. Ordinary website users do not need
these Azure resource roles merely to use the chat application.

### Inherited Administration and Platform Roles

Ken Hyld also has subscription-scoped `Owner`, `Contributor`, `Foundry Owner`,
`Foundry Project Manager`, and `Foundry User` assignments. In particular,
`Owner` is broader than the application-specific roles: it allows management of
resources and Azure RBAC assignments across the subscription. The overlapping
roles are existing assignments, not additional requirements for chat users.

The inventory also found administrator and service-principal assignments at
subscription and parent-management-group scopes, including `Owner` and
`User Access Administrator`, plus Microsoft Defender and platform service roles.
These are inherited platform access paths, not app-created runtime identities.
An environment security review must consider them even though the application
matrix above is narrowly scoped. This document is not a complete tenant-wide
privilege audit or proof of effective access through every group, PIM activation,
directory role, deny assignment, or Azure Policy.

No RBAC assignment conditions were present on the installation-specific
assignments inspected. No permissions were removed or tightened during this
documentation task.

## Entra Applications and Website Access

Azure RBAC, API delegation, and the website's sign-in policy are separate layers.
The managed identities above are not the Entra client and server app registrations.

| Registration | Application/client ID | Service principal/object ID |
| --- | --- | --- |
| Azure Search OpenAI Chat Client App 36152 | `58bbc280-5f64-426b-bfd3-76bd045b6340` | `e0929cd8-74ac-4811-9f26-bdb8ffd738de` |
| Azure Search OpenAI Chat Server App 36152 | `6bc5a096-e139-4b9d-8295-8bb6418f762d` | `d07625f8-081f-4d81-a1a7-4a35e8f87387` |

Both registrations have `signInAudience=AzureADMyOrg`. Both service principals
are enabled, have `appRoleAssignmentRequired=false`, and define no app roles.
They are single-tenant applications; a personal external address does not gain
access merely by knowing the URL. Any external user must first be admitted to
the tenant under its invitation, identity, and sign-in policies.

### Delegated API Permissions and Consent

| Caller | Target | Permission | Verified consent |
| --- | --- | --- | --- |
| Client app | Server API | `access_as_user` | `AllPrincipals` admin consent |
| Client app | Microsoft Graph | `User.Read` | Requested in registration; no separate client-to-Graph grant was returned in this inspection |
| Server app | Microsoft Graph | `User.Read`, `email`, `offline_access`, `openid`, `profile` | `AllPrincipals` admin consent |
| Server app | Azure Cognitive Search | `user_impersonation` | `AllPrincipals` admin consent |

All permissions listed above are delegated scopes, not application-only Graph
permissions or Azure resource roles. `AllPrincipals` is the consent's tenant-wide
scope; it does not make the application anonymous or give every user Azure Owner
rights. The server API exposes `access_as_user`; its audience is
`api://6bc5a096-e139-4b9d-8295-8bb6418f762d`. Search's Entra resource application
ID is `880da380-985e-4198-81b9-e05b1cc53158`.

The scopes support user sign-in/profile information, delegated API access, and
the server's on-behalf-of Search access. `offline_access` permits requesting
refresh tokens. The actual resource access still depends on applicable
authorization; a delegated permission is not itself an Azure RBAC assignment.

### Login Policy and Document Visibility

The deployed settings were verified as:

| Setting | Value / effect |
| --- | --- |
| App Service Easy Auth | Enabled; authentication required |
| Unauthenticated requests | `RedirectToLoginPage` |
| Issuer | `https://login.microsoftonline.com/141346e9-efbf-4062-ab81-a8819181ba24/v2.0` |
| `AZURE_USE_AUTHENTICATION` | `true` |
| `AZURE_ENABLE_UNAUTHENTICATED_ACCESS` | `false` |
| `AZURE_ENFORCE_ACCESS_CONTROL` | `false` |
| Easy Auth principal/application allowlists | No entries configured |
| Enterprise-app assignment requirement | Disabled on both apps |
| User upload | `USE_USER_UPLOAD=false` |

**Important:** login is required, but per-document ACL enforcement is disabled.
An identity accepted by the tenant and application sign-in policies is not
restricted to a personal subset of the shared document index by document ACLs.
There is no application-specific named-user allowlist in the inspected Easy Auth
configuration. Conditional Access, tenant guest policies, or other tenant rules
may still restrict sign-in; those policies were not exhaustively audited.

Ken Hyld's existing directory object is enabled, has `userType=Member`, and has
accepted external-user redemption with an `ExternalAzureAD` identity. The
documented login remains `khyld@microsoft.com`. An invitation sent to an
alternative personal email reused that existing object; its login binding was
not reset. An alternative email is not automatically a separate sign-in identity.

The client app's verified redirect URIs are:

- SPA: `http://localhost:5173/redirect`.
- SPA: `http://localhost:50505/redirect`.
- SPA: `https://app-backend-b32vfnrzdibxy.azurewebsites.net/redirect`.
- Easy Auth: `https://app-backend-b32vfnrzdibxy.azurewebsites.net/.auth/login/aad/callback`.

Keep credential values out of this document. Easy Auth and the Python server
reference secrets through application settings; managed identity use for Search,
Storage, and models does not mean the entire login configuration is secret-free.
Credential values and a full credential-expiry audit are not included here.

## Network and Service Security

| Component | Verified access and protection |
| --- | --- |
| Web app | Public network access enabled; HTTPS-only; TLS minimum 1.2 for app and SCM; IP rules allow all; no VNet integration |
| Search | Public network access enabled; no IP restrictions or private endpoints; `disableLocalAuth=true`, so API-key authentication is disabled |
| Storage | Public network access enabled; firewall default `Allow`; HTTPS-only; TLS minimum 1.2; `allowBlobPublicAccess=false`; `allowSharedKeyAccess=false`; no private endpoints |
| AI Services / OpenAI | Public network access enabled; network ACL default `Allow`; `disableLocalAuth=true` |
| Document Intelligence | Public network access enabled; network ACL default `Allow`; `disableLocalAuth=true` |
| FTP / SCM publishing | Basic publishing credentials disabled for both; FTPS mode `FtpsOnly` does not re-enable those disabled credentials |

Public network access is **not** the same as anonymous data access. Resource data
operations still require the appropriate identity and authorization. Conversely,
requiring Entra authentication is **not** private network isolation. There are no
private endpoint resources in this resource group, and no network allowlist
limits the website to specific client IPs.

Storage permits the `AzureServices` network bypass and has a resource access
entry for the subscription's `Microsoft.Security/datascanners/StorageDataScanner`.
Subscription-level Defender scanning roles were also observed. These are
platform security integrations, not extra permissions of the chat app identity.

### Items to Review Before Production

These are observations and recommendations, not changes made during this task:

1. Decide whether tenant-wide eligible sign-in is appropriate, or whether to
	 require explicit enterprise-app assignment or a narrower application allowlist.
2. Enable and validate document ACL enforcement before storing documents that
	 different signed-in users must not share.
3. Consider narrowing RG-wide service roles to the specific Search, Storage,
	 AI account, or blob container where supported and compatible with the app.
4. Review overlapping subscription Owner/Contributor/Foundry roles and inherited
	 management-group administrators separately from the application identity.
5. Review GlobalStandard model processing locations against residency requirements.
6. Plan private networking, monitoring, credential rotation, budgets, and backup
	 or re-ingestion procedures according to the production requirements.
7. Review the three older Search identities for references before deleting them.

## Operational Notes and Verification

- Serverless Search provisioning required subscription feature
	`Microsoft.Search/Serverless` registration and provider re-registration.
	Italy North failed for this subscription; Poland Central succeeded. Do not
	assume that availability in this snapshot applies to every subscription or region.
- The Search ARM configuration was verified with API `2026-09-01-preview`.
	The service has no fixed dedicated replica/partition configuration in this setup.
- Infrastructure deployment: `abg-search-demo-1792818133`.
- Latest application deployment verified during the repair:
	`728b1503-6e2a-467a-b3f3-f53cf3b08369`; Kudu reported success, complete, and active.
- The retrieval repair ignores the minimum semantic reranker score when semantic
	ranking is disabled. Without it, the frontend's default threshold could discard
	every retrieved document. All 32 tests in the affected chat module passed.
- A live check of the repaired local backend against the deployed Azure services
	returned `Zava blev grundlagt i 1985 [Zava_Company_Overview.md]` with five sources.
- The public website was verified to redirect unauthenticated traffic to Entra.
	A login redirect alone is not proof of a complete authenticated chat flow.
- On 2026-10-24, the user reported successfully signing in to the deployed app
	in their own browser and asking the chatbot questions, with responses appearing
	to work. This completes the user-reported manual login-and-chat smoke test;
	the assistant did not directly observe that browser session or verify citations.
	Authenticator failed in the shared browser, but sign-in succeeded in the user's
	own browser. The cause of the shared-browser failure was not diagnosed.

### Keeping This Snapshot Current

Recheck the resource inventory, model deployment versions/SKUs, managed identity
principal IDs, direct and inherited role assignments, Entra delegated consent,
Easy Auth settings, network access settings, and feature flags after a deployment
or access change. Model auto-upgrades and new ingestion can change this document's
model versions and chunk count without changes to the repository.

The verification used Azure resource inventory, Azure RBAC listing, ARM reads of
service/runtime/authentication configuration, Microsoft Graph app/consent reads,
and Search index metadata/statistics. Local ingestion settings were read from a
whitelist of non-secret azd variables. No secret values or raw environment exports
were persisted as documentation artifacts. Some Azure CLI reads were interrupted
during collection; the remaining ARM/Graph/Search facts were verified through
authenticated REST reads using the existing azd login instead.

## Related Documentation

- [Architecture](architecture.md)
- [Login and document access control](login_and_acl.md)
- [Data ingestion](data_ingestion.md)
- [Low-cost deployment and serverless caveats](deploy_lowcost.md)