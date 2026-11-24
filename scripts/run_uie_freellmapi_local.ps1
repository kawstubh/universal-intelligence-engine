$ErrorActionPreference = "Stop"

# Starts the local UIE API against the Windows FreeLLMAPI desktop service.
# API credentials are requested with hidden input and are kept only in this
# PowerShell process and its child process; they are not written to disk.

$freellmapiKey = Read-Host "FreeLLMAPI unified API key (hidden)" -AsSecureString
$uieKey = Read-Host "Local UIE API key (hidden)" -AsSecureString
$freellmapiPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($freellmapiKey)
$uiePtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($uieKey)

try {
    $env:REASONING_PROVIDER = "freellmapi"
    $env:FREELLMAPI_BASE_URL = "http://127.0.0.1:31415/v1"
    $env:FREELLMAPI_MODEL = "auto"
    $env:FREELLMAPI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($freellmapiPtr)
    $env:UIE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($uiePtr)

    Write-Host "Starting local UIE API on http://127.0.0.1:8000"
    Write-Host "Patient-specific context is blocked from FreeLLMAPI."
    python -m uvicorn universal_intelligence_engine.api:app --host 127.0.0.1 --port 8000
}
finally {
    $env:FREELLMAPI_API_KEY = $null
    $env:UIE_API_KEY = $null
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($freellmapiPtr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($uiePtr)
    $freellmapiKey.Dispose()
    $uieKey.Dispose()
}
