# infra/set-secrets.ps1 - stores the server's secrets in AWS SSM Parameter Store, typed by you, never in git or Terraform.
#
# The server's deploy.sh (user_data.sh.tftpl) reads every parameter under /interview-prep/prod
# and writes each one into its .env as NAME=value. SSM Parameter Store is AWS's free key-value
# store; a "SecureString" parameter is encrypted with AWS's default key for SSM, and only your
# admin user and the server's IAM role (iam.tf) can read it.
#
# Usage, from infra/ (uses your AWS CLI login and region):
#   ./set-secrets.ps1                    makes SECRET_KEY and POSTGRES_PASSWORD (random, once),
#                                        then asks for each missing key (Enter skips one)
#   ./set-secrets.ps1 ANTHROPIC_API_KEY  sets or replaces one value (typed hidden)
#   ./set-secrets.ps1 -List              shows which names are set (never the values)
# After a change, run deploy.sh on the server again (a Session Manager shell, or step 4's deploy).
# POSTGRES_PASSWORD is never replaced: the database was created with it.

param(
    [string]$Name,
    [switch]$List
)

$ErrorActionPreference = "Stop"
$Prefix = "/interview-prep/prod"
$Asked = @(
    "ANTHROPIC_API_KEY", "LANGSMITH_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY",
    "VOYAGE_API_KEY", "TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "GOOGLE_CLIENT_ID"
)

function Get-SetNames {
    $names = aws ssm get-parameters-by-path --path $Prefix --query "Parameters[*].Name" --output text
    if ($LASTEXITCODE -ne 0) { throw "Could not list the parameters (is the AWS CLI logged in?)" }
    if (-not $names) { return @() }
    return @($names -split "\s+" | ForEach-Object { $_.Split("/")[-1] })
}

function Set-Secret([string]$Key, [string]$Value) {
    aws ssm put-parameter --name "$Prefix/$Key" --type SecureString --overwrite --value $Value | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not store $Key" }
    Write-Output "  $Key stored."
}

function Read-Hidden([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function New-RandomValue {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace "[+/=]", "")
}

$set = Get-SetNames

if ($List) {
    Write-Output "Set under ${Prefix}:"
    $set | Sort-Object | ForEach-Object { Write-Output "  $_" }
    exit 0
}

if ($Name) {
    if ($Name -eq "POSTGRES_PASSWORD" -and $set -contains $Name) {
        throw "POSTGRES_PASSWORD is already set and the database uses it: it is never replaced."
    }
    $value = Read-Hidden "$Name"
    if (-not $value) { throw "Nothing typed: $Name unchanged." }
    Set-Secret $Name $value
    exit 0
}

foreach ($key in "SECRET_KEY", "POSTGRES_PASSWORD") {
    if ($set -notcontains $key) { Set-Secret $key (New-RandomValue) }
}
foreach ($key in $Asked) {
    if ($set -contains $key) { continue }
    $value = Read-Hidden "$key (Enter to skip)"
    if ($value) { Set-Secret $key $value }
}
Write-Output "Done. ./set-secrets.ps1 -List shows what is set."
