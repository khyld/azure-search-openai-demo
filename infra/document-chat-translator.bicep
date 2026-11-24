targetScope = 'resourceGroup'

type DocumentServices = {
  storageName: string
  appName: string
  testOperatorId: string
}

@description('Dedicated regional Translator name and custom subdomain.')
param translatorName string

@description('Must match the existing document storage region.')
param location string = 'swedencentral'

@description('Existing document demo and optional test operator. Use an empty testOperatorId outside verification.')
param services DocumentServices

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: services.storageName
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

resource app 'Microsoft.Web/sites@2024-11-01' existing = {
  name: services.appName
}

resource translator 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: translatorName
  location: location
  tags: { application: 'almbrand-documentchat' }
  kind: 'TextTranslation'
  sku: { name: 'S1' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: translatorName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for name in [
  'ab-documentchat-translation-source'
  'ab-documentchat-translation-target'
]: {
  parent: blobs
  name: name
  properties: { publicAccess: 'None' }
}]

resource translatorStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for index in range(0, 2): {
  scope: containers[index]
  name: guid(containers[index].id, translator.id, index == 0 ? 'translator-source-contributor' : 'translator-storage')
  properties: {
    principalId: translator.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}]

resource appStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for index in range(0, 2): {
  scope: containers[index]
  name: guid(containers[index].id, app.id, 'blob-contributor')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}]

resource appTranslator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: translator
  name: guid(translator.id, app.id, 'translator-user')
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}

resource operatorStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for index in range(0, 2): if (!empty(services.testOperatorId)) {
  scope: containers[index]
  name: guid(containers[index].id, services.testOperatorId, 'translation-test')
  properties: {
    principalId: services.testOperatorId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}]

resource operatorTranslator 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(services.testOperatorId)) {
  scope: translator
  name: guid(translator.id, services.testOperatorId, 'translation-test')
  properties: {
    principalId: services.testOperatorId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}

output translatorService string = translator.name
output translatorEndpoint string = 'https://${translator.name}.cognitiveservices.azure.com'