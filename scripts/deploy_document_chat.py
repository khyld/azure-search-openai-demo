import argparse
import io
import json
import os
import subprocess
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import AzureDeveloperCliCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.storage.blob import ContainerClient, ContentSettings
from dotenv_azd import load_azd_env


class DocumentChatDeployment:
    def __init__(self, subscription: str, resource_group: str, tenant: str):
        self.credential = AzureDeveloperCliCredential(tenant_id=tenant, process_timeout=60)
        self.root = f"https://management.azure.com/subscriptions/{subscription}/resourceGroups/{resource_group}"
        self.tenant = tenant

    def request(self, method: str, url: str, scope: str = "https://management.azure.com/.default", **kwargs):
        headers = {"Authorization": "Bearer " + self.credential.get_token(scope).token, **kwargs.pop("headers", {})}
        response = requests.request(method, url, headers=headers, timeout=(30, 180), **kwargs)
        if response.status_code >= 400:
            code = "AzureError"
            try:
                code = response.json().get("error", {}).get("code", code)
            except (ValueError, AttributeError):
                pass
            raise RuntimeError(f"Azure operation failed: HTTP {response.status_code}, {code}")
        return response

    def arm(self, method: str, path: str, **kwargs):
        return self.request(method, self.root + path, **kwargs).json()

    def configure_index(self, search_name: str):
        with SearchIndexClient(f"https://{search_name}.search.windows.net", self.credential) as client:
            try:
                existing = client.get_index("ab-documentchat")
                fields = {field.name: field for field in existing.fields}
                if not all(name in fields and fields[name].filterable for name in ("owner_id", "document_id")):
                    raise RuntimeError("Existing document chat index has an incompatible ownership schema")
                if fields.get("embedding") is None or fields["embedding"].vector_search_dimensions != 3072:
                    raise RuntimeError("Existing document chat index has incompatible embedding dimensions")
                return
            except ResourceNotFoundError:
                pass
            client.create_index(
                SearchIndex(
                    name="ab-documentchat",
                    fields=[
                        SimpleField(name="id", type="Edm.String", key=True),
                        SimpleField(name="owner_id", type="Edm.String", filterable=True),
                        SimpleField(name="document_id", type="Edm.String", filterable=True),
                        SimpleField(name="page", type="Edm.Int32"),
                        SearchableField(name="content", type="Edm.String"),
                        SearchField(
                            name="embedding",
                            type="Collection(Edm.Single)",
                            searchable=True,
                            vector_search_dimensions=3072,
                            vector_search_profile_name="embedding-profile",
                        ),
                    ],
                    vector_search=VectorSearch(
                        algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
                        profiles=[VectorSearchProfile(name="embedding-profile", algorithm_configuration_name="hnsw")],
                    ),
                )
            )

    def add_callback(self, client_id: str, app_name: str):
        endpoint = "https://graph.microsoft.com/v1.0/applications"
        graph_scope = "https://graph.microsoft.com/.default"
        result = self.request(
            "GET", endpoint, graph_scope, params={"$filter": f"appId eq '{client_id}'", "$select": "id,web"}
        ).json()
        application = result["value"][0]
        redirects = application["web"]["redirectUris"]
        callback = f"https://{app_name}.azurewebsites.net/.auth/login/aad/callback"
        if callback not in redirects:
            self.request(
                "PATCH",
                endpoint + "/" + application["id"],
                graph_scope,
                json={"web": {"redirectUris": [*redirects, callback]}},
            )

    def provision(self, arguments):
        source_path = f"/providers/Microsoft.Web/sites/{arguments.source_app}"
        source_app = self.arm("GET", source_path + "?api-version=2024-11-01")
        settings = self.arm("POST", source_path + "/config/appsettings/list?api-version=2024-11-01")["properties"]
        authentication = self.arm("GET", source_path + "/config/authsettingsV2/list?api-version=2024-11-01")[
            "properties"
        ]
        registration = authentication["identityProviders"]["azureActiveDirectory"]["registration"]
        if arguments.app_name == arguments.source_app or arguments.storage_name == settings["AZURE_STORAGE_ACCOUNT"]:
            raise RuntimeError("The document chat must not replace the original app or storage")
        values = {**os.environ, **settings}
        parameters = {
            "appName": arguments.app_name,
            "storageName": arguments.storage_name,
            "services": {
                "planName": source_app["properties"]["serverFarmId"].rsplit("/", 1)[1],
                "searchName": values["AZURE_SEARCH_SERVICE"],
                "openAiName": values["AZURE_OPENAI_SERVICE"],
                "documentIntelligenceName": values["AZURE_DOCUMENTINTELLIGENCE_SERVICE"],
                "chatDeployment": values["AZURE_OPENAI_CHATGPT_DEPLOYMENT"],
                "embeddingDeployment": values["AZURE_OPENAI_EMB_DEPLOYMENT"],
            },
            "entra": {
                "tenantId": self.tenant,
                "clientAppId": registration["clientId"],
                "serverAppId": values["AZURE_SERVER_APP_ID"],
                "clientSecret": settings[registration["clientSecretSettingName"]],
            },
        }
        bicep = Path.home() / ".azure/bin/bicep.exe"
        command = str(bicep) if bicep.exists() else "bicep"
        template = json.loads(
            subprocess.check_output([command, "build", "infra/document-chat.bicep", "--stdout"], text=True)
        )
        deployment_path = "/providers/Microsoft.Resources/deployments/almbrand-documentchat?api-version=2024-03-01"
        payload = {
            "properties": {
                "mode": "Incremental",
                "template": template,
                "parameters": {name: {"value": value} for name, value in parameters.items()},
            }
        }
        validation = self.request("POST", self.root + deployment_path.replace("?", "/validate?"), json=payload)
        operation = validation.headers.get("Azure-AsyncOperation")
        validation_deadline = time.monotonic() + 600
        while operation:
            result = self.request("GET", operation).json()
            state = result.get("status")
            if state == "Succeeded":
                break
            if state in ("Failed", "Canceled") or time.monotonic() > validation_deadline:
                raise RuntimeError("ARM validation did not succeed; no resources were provisioned")
            time.sleep(5)
        print("ARM validation: Succeeded", flush=True)
        self.add_callback(parameters["entra"]["clientAppId"], arguments.app_name)
        self.configure_index(values["AZURE_SEARCH_SERVICE"])
        self.arm("PUT", deployment_path, json=payload)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            result = self.arm("GET", deployment_path)
            state = result["properties"]["provisioningState"]
            if state == "Succeeded":
                print("Infrastructure: Succeeded", flush=True)
                return
            if state in ("Failed", "Canceled"):
                raise RuntimeError(
                    "Infrastructure deployment failed; inspect the almbrand-documentchat deployment operations"
                )
            time.sleep(10)
        raise RuntimeError("Infrastructure is still provisioning; inspect the deployment before retrying")

    def deploy_code(self, app_name: str):
        archive = io.BytesIO()
        build_id = uuid.uuid4().hex
        backend = Path("app/backend")
        if not (backend / "static/documentchat.html").exists():
            raise RuntimeError("Build the frontend before deploying")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for source in backend.rglob("*"):
                if not source.is_file() or any(
                    part.startswith((".env", ".venv", "__pycache__", ".pytest_cache"))
                    for part in source.relative_to(backend).parts
                ):
                    continue
                if source.name == "documentchat-build.json" or source.suffix in (".pyc", ".map", ".pem", ".key", ".pfx"):
                    continue
                package.write(source, source.relative_to(backend))
            package.writestr("documentchat-build.json", json.dumps({"id": build_id}))
        scm = f"https://{app_name}.scm.azurewebsites.net"
        previous = self.request("GET", scm + "/api/deployments").json()
        previous_id = previous[0]["id"] if previous else None
        print("Uploading build: " + build_id, flush=True)
        self.request(
            "POST",
            scm + "/api/zipdeploy?isAsync=true",
            data=archive.getvalue(),
            headers={"Content-Type": "application/zip"},
        )
        print("Code uploaded; waiting for the remote build", flush=True)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            deployments = self.request("GET", scm + "/api/deployments").json()
            if deployments and deployments[0]["id"] != previous_id:
                deployment = deployments[0]
                if deployment.get("status") == 4 and deployment.get("complete"):
                    print("Application deployment: " + deployment["id"], flush=True)
                    return build_id
                if deployment.get("status") == 3:
                    raise RuntimeError(
                        "Remote build failed; inspect the deployment logs (do not export application settings)"
                    )
            time.sleep(15)
        raise RuntimeError("Remote build still running; verify Kudu before starting another deployment")

    def restart(self, app_name: str):
        self.request("POST", self.root + f"/providers/Microsoft.Web/sites/{app_name}/restart?api-version=2024-11-01")
        print("Document application restart requested", flush=True)

    def status(self, app_name: str):
        deployments = self.request("GET", f"https://{app_name}.scm.azurewebsites.net/api/deployments").json()
        latest = deployments[0] if deployments else {}
        print(json.dumps({"deployment": {key: latest.get(key) for key in ("id", "status", "complete", "active")}}), flush=True)
        health = requests.get(f"https://{app_name}.azurewebsites.net/healthz?artifacts=1", timeout=90, allow_redirects=False)
        result = {"http_status": health.status_code, "build": health.headers.get("X-DocumentChat-Build")}
        if health.status_code == 200:
            payload = health.json()
            result["artifacts"] = payload.get("artifacts")
        print(json.dumps(result), flush=True)

    def approve_pilot_budget(self, storage_name: str):
        ledger = {
            "version": 1, "month": datetime.now(timezone.utc).strftime("%Y-%m"),
            "rate_card": "documentchat-2026-10-v1", "limit_ore": 100_000,
            "overhead_ore": 90_000, "reservations": {},
            "approval": "User-approved single-user pilot; 100 DKK artifact allocation, 900 DKK held for other costs",
        }
        with ContainerClient(f"https://{storage_name}.blob.core.windows.net", "ab-documentchat", self.credential) as container:
            container.upload_blob(
                "coordination/artifact-budget.json", json.dumps(ledger).encode(), overwrite=False,
                content_settings=ContentSettings(content_type="application/json"),
            )
        print("Approved monthly pilot ledger created once: 100 DKK artifact allocation; no existing ledger overwritten", flush=True)

    def verify(self, app_name: str, build_id: str):
        deadline = time.monotonic() + 600
        url = f"https://{app_name}.azurewebsites.net"
        while time.monotonic() < deadline:
            try:
                health = requests.get(url + "/healthz", timeout=30, allow_redirects=False)
                if (
                    health.status_code == 200
                    and health.headers.get("X-DocumentChat-Build") == build_id
                    and health.json().get("status") == "ok"
                ):
                    login = requests.get(url + "/", timeout=30, allow_redirects=False)
                    if login.status_code not in (302, 401):
                        raise RuntimeError("The website did not require login")
                    print("Active build, readiness and login enforcement: verified", flush=True)
                    print("Build: " + build_id, flush=True)
                    print(url + "/", flush=True)
                    return
            except (requests.RequestException, ValueError):
                pass
            time.sleep(10)
        raise RuntimeError("Application not ready; inspect runtime logs and managed identity role propagation")


def main():
    parser = argparse.ArgumentParser(
        description="Deploy the isolated Alm. Brand document demo without replacing the existing demo"
    )
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--source-app", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--storage-name", required=True)
    parser.add_argument("--code-only", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--approve-pilot-budget", action="store_true")
    arguments = parser.parse_args()
    if arguments.app_name == arguments.source_app:
        parser.error("The document deployment must not target the original application")
    if arguments.approve_pilot_budget and (arguments.status_only or arguments.restart or arguments.code_only):
        parser.error("Budget approval must run separately from deployment or status actions")
    os.environ["AZURE_DEV_USER_AGENT"] = "microsoft_foundry_skill"
    load_azd_env(override=False)
    deployment = DocumentChatDeployment(arguments.subscription, arguments.resource_group, arguments.tenant)
    try:
        if arguments.approve_pilot_budget:
            deployment.approve_pilot_budget(arguments.storage_name)
            return
        if arguments.status_only:
            deployment.status(arguments.app_name)
            return
        if not arguments.code_only:
            deployment.provision(arguments)
        build_id = deployment.deploy_code(arguments.app_name)
        if arguments.restart:
            deployment.restart(arguments.app_name)
        deployment.verify(arguments.app_name, build_id)
    finally:
        deployment.credential.close()


if __name__ == "__main__":
    main()
