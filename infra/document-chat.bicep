targetScope = 'resourceGroup'

type ExistingServices = {
  planName: string
  searchName: string
  openAiName: string
  documentIntelligenceName: string
  chatDeployment: string
  embeddingDeployment: string
}

@secure()
type EntraConfiguration = {
  tenantId: string
  clientAppId: string
  serverAppId: string
  clientSecret: string
}

@description('Dedicated web app name. Existing applications are not modified.')
param appName string

@description('Dedicated storage account with no recoverable document versions.')
param storageName string

param location string = 'swedencentral'
param services ExistingServices

param entra EntraConfiguration

var indexName = 'ab-documentchat'
var containerName = 'ab-documentchat'

resource plan 'Microsoft.Web/serverfarms@2024-11-01' existing = {
  name: services.planName
}

resource search 'Microsoft.Search/searchServices@2026-09-01-preview' existing = {
  name: services.searchName
}

resource openAi 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: services.openAiName
}

resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: services.documentIntelligenceName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  tags: { application: 'almbrand-documentchat' }
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: { defaultAction: 'Allow', bypass: 'None' }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: false
    deleteRetentionPolicy: { enabled: false }
    containerDeleteRetentionPolicy: { enabled: false }
  }
}

resource container 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: { publicAccess: 'None' }
}

resource app 'Microsoft.Web/sites@2024-11-01' = {
  name: appName
  tags: { application: 'almbrand-documentchat' }
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'python3 -m gunicorn --workers 1 --worker-class custom_uvicorn_worker.CustomUvicornWorker --timeout 230 --bind 0.0.0.0:8000 documentchat_main:app'
      alwaysOn: true
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      http20Enabled: true
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'RUNNING_IN_PRODUCTION', value: 'true' }
        { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
        { name: 'DOCUMENTCHAT_STORAGE_CONTAINER', value: containerName }
        { name: 'AZURE_SEARCH_SERVICE', value: search.name }
        { name: 'DOCUMENTCHAT_SEARCH_INDEX', value: indexName }
        { name: 'AZURE_OPENAI_SERVICE', value: openAi.name }
        { name: 'AZURE_DOCUMENTINTELLIGENCE_SERVICE', value: documentIntelligence.name }
        { name: 'AZURE_OPENAI_CHATGPT_DEPLOYMENT', value: services.chatDeployment }
        { name: 'AZURE_OPENAI_EMB_DEPLOYMENT', value: services.embeddingDeployment }
        { name: 'AZURE_AUTH_TENANT_ID', value: entra.tenantId }
        { name: 'AZURE_CLIENT_APP_ID', value: entra.clientAppId }
        { name: 'AZURE_SERVER_APP_ID', value: entra.serverAppId }
        { name: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET', value: entra.clientSecret }
        { name: 'PYTHONUNBUFFERED', value: '1' }
      ]
    }
  }
}

resource authentication 'Microsoft.Web/sites/config@2024-11-01' = {
  parent: app
  name: 'authsettingsV2'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      requireAuthentication: true
      excludedPaths: [ '/healthz' ]
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entra.clientAppId
          clientSecretSettingName: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET'
          openIdIssuer: '${environment().authentication.loginEndpoint}${entra.tenantId}/v2.0'
        }
        login: { loginParameters: [ 'scope=api://${entra.serverAppId}/.default openid profile email offline_access' ] }
        validation: { allowedAudiences: [ 'api://${entra.serverAppId}' ] }
      }
    }
    login: { tokenStore: { enabled: true } }
    httpSettings: { requireHttps: true }
  }
}

resource ftpPolicy 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: app
  name: 'ftp'
  properties: { allow: false }
}

resource scmPolicy 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-11-01' = {
  parent: app
  name: 'scm'
  properties: { allow: false }
}

resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: container
  name: guid(container.id, app.id, 'blob-contributor')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}

resource searchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, app.id, 'index-data-contributor')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
  }
}

resource openAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openAi
  name: guid(openAi.id, app.id, 'openai-user')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  }
}

resource documentIntelligenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: documentIntelligence
  name: guid(documentIntelligence.id, app.id, 'document-intelligence-user')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}

output appUrl string = 'https://${app.properties.defaultHostName}'
output principalId string = app.identity.principalId
output storageAccount string = storage.name
output searchIndex string = indexName