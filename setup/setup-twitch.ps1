# Foxfire Lodge wall - one-time Twitch sign-in (run on your own PC in PowerShell).
# It opens Twitch in your browser so you can approve the app, then puts the two secrets the GitHub job needs on your clipboard,
# one at a time, so you can paste them into GitHub. Nothing is printed or saved to disk.
#   Right-click this file > Run with PowerShell   (or:  powershell -ExecutionPolicy Bypass -File .\setup-twitch.ps1)
# It asks for the Client ID and Client Secret (the secret is hidden while you paste it).
$ErrorActionPreference = "Stop"
$ClientId = (Read-Host "Paste your Twitch app's Client ID").Trim()
$sec = Read-Host "Paste your Twitch app's Client Secret (hidden)" -AsSecureString
$ClientSecret = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()
Add-Type -AssemblyName System.Web
$redirect = "http://localhost:3000/"
$scopes = "channel:read:redemptions channel:manage:redemptions"
$state = [guid]::NewGuid().ToString("N")

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($redirect)
$listener.Start()
$auth = "https://id.twitch.tv/oauth2/authorize?response_type=code&client_id=$ClientId&redirect_uri=$([uri]::EscapeDataString($redirect))&scope=$([uri]::EscapeDataString($scopes))&state=$state&force_verify=true"
Write-Host "Opening Twitch in your browser. Sign in as your channel and click Authorize..."
Start-Process $auth

$ctx = $listener.GetContext()
$q = [System.Web.HttpUtility]::ParseQueryString($ctx.Request.Url.Query)
$msg = if ($q["code"] -and $q["state"] -eq $state) { "Done! You can close this tab and go back to PowerShell." } else { "Something went wrong. Go back to PowerShell." }
$bytes = [Text.Encoding]::UTF8.GetBytes("<html><body style='font-family:sans-serif;padding:40px'><h2>Foxfire Lodge wall</h2><p>$msg</p></body></html>")
$ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length); $ctx.Response.Close(); $listener.Stop()
if (-not $q["code"] -or $q["state"] -ne $state) { throw "Twitch didn't return an authorization code ($($q['error_description']))." }

$tok = Invoke-RestMethod -Method Post -Uri "https://id.twitch.tv/oauth2/token" -Body @{
    client_id = $ClientId; client_secret = $ClientSecret; code = $q["code"]; grant_type = "authorization_code"; redirect_uri = $redirect
}
Set-Clipboard -Value $tok.refresh_token
Read-Host "1/2  The Twitch refresh token is on your clipboard. Paste it into GitHub as the secret TWITCH_REFRESH_TOKEN, then press Enter"

$key = New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($key)
Set-Clipboard -Value ([Convert]::ToBase64String($key).Replace('+', '-').Replace('/', '_'))
Read-Host "2/2  A new encryption key is on your clipboard. Paste it into GitHub as the secret TWITCH_STATE_KEY, then press Enter"
Set-Clipboard -Value " "
Write-Host "All set. The clipboard has been cleared."
