param([Parameter(Mandatory = $true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
# Synthetic audio, real offline ASR. Uses already-installed FFmpeg/CMU Flite.
$text = 'The quick brown fox jumps over the lazy dog. This is a local speech recognition test.'
& ffmpeg -hide_banner -loglevel error -f lavfi -i "flite=text=${text}:voice=slt" -ar 16000 -ac 1 -c:a pcm_s16le -y $OutputPath
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg speech generation failed' }
Get-FileHash -Algorithm SHA256 $OutputPath | Format-List
