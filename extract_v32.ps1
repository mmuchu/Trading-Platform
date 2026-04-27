# extract_v32.ps1 - v3.2 Deployment Extractor
 $b64 = [IO.File]::ReadAllText("_v32.b64")
Write-Host "Base64 length: $($b64.Length) chars (expected ~42388)"
Write-Host "Decoding..."
 $zipBytes = [Convert]::FromBase64String($b64)
Write-Host "Zip size: $($zipBytes.Length) bytes"
[IO.File]::WriteAllBytes("_v32.zip", $zipBytes)
Write-Host "Zip written. Extracting..."
Add-Type -AssemblyName System.IO.Compression.FileSystem
 $zip = [System.IO.Compression.ZipFile]::OpenRead("$PWD\_v32.zip")
 $extracted = 0
foreach ($entry in $zip.Entries) {
    $dest = Join-Path $PWD $entry.FullName
    $dir = Split-Path $dest
    if (!(Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $stream = $entry.Open()
    $file = [IO.File]::Create($dest)
    $stream.CopyTo($file)
    $file.Close()
    $stream.Close()
    $extracted++
    Write-Host "  Written: $($entry.FullName) ($($entry.Length) bytes)"
}
 $zip.Dispose()
Write-Host ""
Write-Host "SUCCESS! v3.2 deployed - $extracted files extracted"
Write-Host "Verify with: Select-String -Path orchestrator_v3.py -Pattern 'v3.2'"
