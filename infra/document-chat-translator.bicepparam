using './document-chat-translator.bicep'

param translatorName = 'cog-trdoc-b32vfnrzdibxy'
param location = 'swedencentral'
param services = {
  storageName: 'stdocb32vfnrzdibxy'
  appName: 'app-docchat-b32vfnrzdibxy'
  testOperatorId: readEnvironmentVariable('DOCUMENTCHAT_TEST_OPERATOR_ID', '')
}