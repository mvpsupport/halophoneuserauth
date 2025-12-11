@description('Base name used for all resources.')
param baseName string

@description('Azure region for deployment.')
param location string = resourceGroup().location

@description('Azure AD tenant ID used for Graph authentication.')
param graphTenantId string

@description('Azure AD application (client) ID used for Graph authentication.')
param graphClientId string

@description('Base64 encoded PFX certificate for Graph client credentials.')
@secure()
param graphCertPfx string

@description('Password protecting the Graph certificate PFX.')
@secure()
param graphCertPassword string

@description('RingCentral Client ID.')
@secure()
param ringCentralClientId string

@description('RingCentral Client Secret.')
@secure()
param ringCentralClientSecret string

@description('RingCentral JWT token.')
@secure()
param ringCentralJwt string

@description('RingCentral sender phone number.')
param ringCentralFromNumber string

var storageAccountName = toLower('${baseName}sa${uniqueString(resourceGroup().id)}')
var functionAppName = toLower('${baseName}-fn')
var appInsightsName = '${baseName}-appi'
var keyVaultName = toLower('${baseName}-kv')

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    IngestionMode: 'ApplicationInsights'
  }
}

resource hostingPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${baseName}-plan'
  location: location
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  kind: 'functionapp'
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    enableRbacAuthorization: false
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: true
    sku: {
      family: 'A'
      name: 'standard'
    }
    accessPolicies: []
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Enabled'
  }
}

resource graphPfxSecret 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  name: '${keyVault.name}/graph-cert-pfx'
  properties: {
    value: graphCertPfx
  }
  dependsOn: [
    keyVault
  ]
}

resource graphPfxPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  name: '${keyVault.name}/graph-cert-password'
  properties: {
    value: graphCertPassword
  }
  dependsOn: [
    keyVault
  ]
}

resource ringCentralClientIdSecret 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  name: '${keyVault.name}/ringcentral-client-id'
  properties: {
    value: ringCentralClientId
  }
  dependsOn: [
    keyVault
  ]
}

resource ringCentralClientSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  name: '${keyVault.name}/ringcentral-client-secret'
  properties: {
    value: ringCentralClientSecret
  }
  dependsOn: [
    keyVault
  ]
}

resource ringCentralJwtSecret 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  name: '${keyVault.name}/ringcentral-jwt'
  properties: {
    value: ringCentralJwt
  }
  dependsOn: [
    keyVault
  ]
}

resource ringCentralFromNumberSecret 'Microsoft.KeyVault/vaults/secrets@2023-02-01' = {
  name: '${keyVault.name}/ringcentral-from-number'
  properties: {
    value: ringCentralFromNumber
  }
  dependsOn: [
    keyVault
  ]
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly: true
    siteConfig: {
      appSettings: [
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${listKeys(storage.name, storage.apiVersion).keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'KEY_VAULT_NAME'
          value: keyVault.name
        }
        {
          name: 'GRAPH_TENANT_ID'
          value: graphTenantId
        }
        {
          name: 'GRAPH_CLIENT_ID'
          value: graphClientId
        }
      ]
    }
  }
  dependsOn: [
    storage
    appInsights
  ]
}

resource kvAccessPolicy 'Microsoft.KeyVault/vaults/accessPolicies@2023-02-01' = {
  name: '${keyVault.name}/add'
  properties: {
    accessPolicies: [
      {
        tenantId: subscription().tenantId
        objectId: functionApp.identity.principalId
        permissions: {
          secrets: [
            'get',
            'list'
          ]
        }
      }
    ]
  }
  dependsOn: [
    keyVault
    functionApp
  ]
}

output functionAppName string = functionApp.name
output keyVaultName string = keyVault.name
output appInsightsName string = appInsights.name
