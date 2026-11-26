# infra/server.ps1 - your on/off switch for the AWS server: "down" stops paying for it without losing data, "up" brings it back.
#
#   ./server.ps1 status   is it running, its address, when the last backup was made
#   ./server.ps1 down     backs the database up to S3, then destroys the server, its disk and its
#                         IP: about $0.50 a month left (the domain's DNS zone)
#   ./server.ps1 up       recreates the server (about 10 minutes): its first boot loads the latest
#                         backup into the new database and starts the latest image from GHCR
#
# How: the switch is the Terraform variable server_enabled (main.tf), written here to
# server.auto.tfvars (git-ignored; Terraform reads *.auto.tfvars by itself), then `terraform
# apply`, which shows what it will create or destroy and asks you to type "yes". The backup runs
# on the server through SSM Run Command (infra/backup.sh); if it fails, nothing is destroyed.
# What stays while the server is down: the backups (S3), the secrets (SSM), the network, the
# roles, the domain and its records, the budget, GitHub's deploy access. A push to main while
# it is down publishes the image and skips the deploy; `up` then starts from that image.
#
# Needs: `aws sso login` first; run from anywhere (it moves to infra/ itself).

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("status", "down", "up")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Name = "interview-prep"

function Assert-Login {
    aws sts get-caller-identity --query Arn --output text | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Not logged in to AWS: run 'aws sso login' first." }
}

function Get-Server {
    $out = aws ec2 describe-instances `
        --filters "Name=tag:Name,Values=$Name" "Name=instance-state-name,Values=pending,running,stopping,stopped" `
        --query "Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress] | [0]" --output text
    if ($LASTEXITCODE -ne 0) { throw "Could not list the servers." }
    if (-not $out -or $out -eq "None") { return $null }
    $parts = $out -split "\s+"
    return [pscustomobject]@{ Id = $parts[0]; State = $parts[1]; Ip = $parts[2] }
}

function Get-LastBackup {
    $account = aws sts get-caller-identity --query Account --output text
    $line = aws s3 ls "s3://$Name-backups-$account/interview/latest.dump" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $line) { return "none yet" }
    return ($line -split "\s+")[0..1] -join " "
}

# Runs shell commands on the server through SSM Run Command and returns $true on success.
function Invoke-OnServer([string]$InstanceId, [string[]]$Commands) {
    $file = Join-Path $env:TEMP "interview-ssm-command.json"
    $json = @{ commands = $Commands } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($file, $json, (New-Object Text.UTF8Encoding($false)))
    $id = aws ssm send-command --instance-ids $InstanceId --document-name AWS-RunShellScript `
        --parameters "file://$file" --query Command.CommandId --output text
    if ($LASTEXITCODE -ne 0) { throw "SSM could not send the command." }
    $status = "Pending"
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 5
        $status = aws ssm get-command-invocation --command-id $id --instance-id $InstanceId --query Status --output text 2>$null
        if ($status -notin @("Pending", "InProgress", "Delayed", $null, "")) { break }
    }
    aws ssm get-command-invocation --command-id $id --instance-id $InstanceId --query StandardOutputContent --output text
    return ($status -eq "Success")
}

function Set-Switch([bool]$On) {
    $value = if ($On) { "true" } else { "false" }
    $text = "# server.auto.tfvars - written by server.ps1 (git-ignored): is the AWS server on?`nserver_enabled = $value`n"
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot "server.auto.tfvars"), $text, (New-Object Text.UTF8Encoding($false)))
}

Assert-Login

switch ($Action) {
    "status" {
        $server = Get-Server
        if ($server) { Write-Output "Server: $($server.Id), $($server.State), http://$($server.Ip)" }
        else { Write-Output "Server: off (destroyed; about `$0.50 a month left)" }
        Write-Output "Last backup: $(Get-LastBackup)"
    }

    "down" {
        $server = Get-Server
        if ($server) {
            if ($server.State -ne "running") {
                throw "The server is $($server.State). Start it first (aws ec2 start-instances --instance-ids $($server.Id)), wait 2 minutes, then run down again, so it can make a backup."
            }
            Write-Output "Backing up the database to S3..."
            if (-not (Invoke-OnServer $server.Id @("/opt/interview/backup.sh"))) {
                throw "The backup failed: nothing was destroyed. Read the output above."
            }
            Write-Output "Backup done ($(Get-LastBackup))."
        } else {
            Write-Output "No server is running; switching Terraform to off anyway."
        }
        Set-Switch $false
        Write-Output "Terraform now shows what it will destroy (the server, its disk, its IP). Type yes to go on."
        terraform apply
        if ($LASTEXITCODE -ne 0) { throw "terraform apply did not finish: run './server.ps1 status' to see where things are." }
        Write-Output "The server is off. './server.ps1 up' brings it back with this backup."
    }

    "up" {
        Set-Switch $true
        Write-Output "Terraform now shows what it will create (the server, its disk, its IP). Type yes to go on."
        terraform apply
        if ($LASTEXITCODE -ne 0) { throw "terraform apply did not finish: run './server.ps1 status' to see where things are." }
        $ip = terraform output -raw public_ip
        Write-Output "The server is starting at $ip. Its first boot installs Docker, restores the last backup and starts the app (about 10 minutes)..."
        for ($i = 0; $i -lt 90; $i++) {
            Start-Sleep -Seconds 10
            try {
                $reply = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://$ip/health"
                if ($reply.StatusCode -eq 200) { Write-Output "Up: $($reply.Content)"; exit 0 }
            } catch { }
        }
        throw "No answer from http://$ip/health after 15 minutes. Open a shell (terraform output shell) and read /var/log/cloud-init-output.log and /var/log/interview-deploy.log."
    }
}
