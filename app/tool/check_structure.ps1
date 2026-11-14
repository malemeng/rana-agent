$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$failures = [System.Collections.Generic.List[string]]::new()

$required = @(
    'pubspec.yaml',
    'analysis_options.yaml',
    'README.md',
    'lib/main.dart',
    'lib/app.dart',
    'lib/app/providers.dart',
    'lib/app/shell.dart',
    'android/settings.gradle.kts',
    'android/build.gradle.kts',
    'android/app/build.gradle.kts',
    'android/app/src/main/AndroidManifest.xml',
    'android/app/src/main/kotlin/com/rana/agent/MainActivity.kt',
    'android/gradlew',
    'android/gradlew.bat',
    'android/gradle/wrapper/gradle-wrapper.jar',
    'android/gradle/wrapper/gradle-wrapper.properties',
    'test/widget_test.dart',
    'test/core/voice_frame_codec_test.dart',
    'test/features/voice/voice_controller_test.dart'
)
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) {
        $failures.Add("缺少文件: $relative")
    }
}

$dartFiles = Get-ChildItem -LiteralPath (Join-Path $root 'lib'),(Join-Path $root 'test') -Recurse -Filter '*.dart' -File
$importPattern = [regex]'import\s+''([^'']+)'''
$importCount = 0
foreach ($file in $dartFiles) {
    $source = [System.IO.File]::ReadAllText($file.FullName)
    foreach ($match in $importPattern.Matches($source)) {
        $uri = $match.Groups[1].Value
        $target = $null
        if ($uri.StartsWith('package:personal_agent/')) {
            $target = Join-Path $root ('lib/' + $uri.Substring('package:personal_agent/'.Length))
        } elseif (-not $uri.StartsWith('dart:') -and -not $uri.StartsWith('package:')) {
            $target = Join-Path $file.DirectoryName $uri
        }
        if ($null -ne $target) {
            $importCount++
            $resolved = [System.IO.Path]::GetFullPath($target)
            if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
                $failures.Add("导入目标不存在: $($file.FullName) -> $uri")
            }
        }
    }
}

$xmlFiles = Get-ChildItem -LiteralPath (Join-Path $root 'android') -Recurse -Filter '*.xml' -File
foreach ($file in $xmlFiles) {
    try {
        [xml]([System.IO.File]::ReadAllText($file.FullName)) | Out-Null
    } catch {
        $failures.Add("XML 无效: $($file.FullName): $($_.Exception.Message)")
    }
}

$pubspec = [System.IO.File]::ReadAllText((Join-Path $root 'pubspec.yaml'))
$dependencies = @(
    'flutter_riverpod', 'dio', 'web_socket_channel', 'shared_preferences',
    'flutter_secure_storage', 'record', 'flutter_pcm_sound', 'permission_handler'
)
foreach ($dependency in $dependencies) {
    if ($pubspec -notmatch "(?m)^  $([regex]::Escape($dependency)):") {
        $failures.Add("pubspec 缺少依赖: $dependency")
    }
}

$wrapper = Join-Path $root 'android/gradle/wrapper/gradle-wrapper.jar'
if (Test-Path -LiteralPath $wrapper -PathType Leaf) {
    $bytes = [System.IO.File]::ReadAllBytes($wrapper)
    if ($bytes.Length -lt 4 -or $bytes[0] -ne 0x50 -or $bytes[1] -ne 0x4B) {
        $failures.Add('Gradle Wrapper JAR 签名无效')
    }
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    exit 1
}

"Dart files: $($dartFiles.Count)"
"Resolved local imports: $importCount"
"Android XML files: $($xmlFiles.Count)"
"Required files: $($required.Count)"
'Static structure check: PASS'
