# Face Manager for Android

The standalone Android APK runs the same React interface, FastAPI services and
SQLite library workflows as the PC application on the phone. This includes the
world map, images and folder filters, face review and suggestions, people,
filename previews/renaming, background tasks, settings, and database backup and
restore. No running PC or hosted photo service is required.

The source supports local Android builds for development. Face Manager's automated builds and GitHub releases target Windows and Ubuntu.

Local builds require Android 11/API 30 or newer and support ARM64 and x86-64.

Recognition models and the offline world map are bundled. Photos and face vectors
are processed locally. Internet permission supports the private loopback interface
and optional GitHub update checks/downloads; photo analysis needs no connection.
The local API binds only to 127.0.0.1 on a random port and requires a per-process
session token. The WebView cannot navigate to arbitrary remote sites.

The library and previews are private app data, separate from the photo originals.
Uninstalling/clearing app data removes the library; use the in-app database export
first. Database backups contain metadata and paths, not copies of the original
photos. A backup restored on a different device still needs its referenced photos
at accessible paths. File renaming acts on the selected original files, just as on
PC. Android cloud/device-transfer backup of private app data is disabled.

Allow notifications when asked to see background status and the **Beenden** action.
Photo management also works when notifications are denied. Android can interrupt
background processing; persisted imports recover on the next start. Android 15 and newer
limit background data-sync service time to six hours per 24 hours; bringing the app
to the foreground resets this budget. Keep the application open for very
large imports. **Beenden** in the notification stops the local backend gracefully.
Android's package installer handles installation and update confirmation for local APKs.

## Build and validate

Requirements: JDK 17, Python 3.11, Node 20+, Android SDK 35/build-tools 35.0.0,
NDK 27.0.12077973 and CMake 3.22.1. Set `ANDROID_HOME` or the ignored
`android/local.properties` (`sdk.dir=/path/to/sdk`). `ANDROID_BUILD_PYTHON` can
select the Python 3.11 executable. Gradle stages the exact shared backend, frontend,
VERSION and CHANGELOG on every relevant source change.

```sh
npm --prefix frontend ci
./scripts/check-all.sh
./scripts/check-android.sh
# With emulator/device visible in adb:
ANDROID_CONNECTED_TESTS=1 ./scripts/check-android.sh
./scripts/package-android.sh
```

The wrapper and model downloads verify fixed SHA-256 checksums. Downloads happen
during the build, not on the phone. The native HNSW index uses the same upstream
implementation as the desktop and grows its allocation as the library grows.
ArcFace uses the PC application's 512-dimensional buffalo_l recognition weights;
its historical tensor channel order is preserved for embedding compatibility.
The detector uses mobile-native YuNet. Pretrained InsightFace weights retain the
upstream non-commercial research model policy; see `assets/NOTICE.txt`. The code
licences do not replace the upstream model usage terms.

The local verification script runs JVM tests, Android lint, and packaging. Use a connected device or emulator for instrumentation.
Android Test Orchestrator clears each test's application data. Debug builds alone
include the shared backend regression tests and test helpers. The real-person
fixtures and sources are documented under `app/src/androidTest/assets/README.md`;
tests/helpers/fixtures are excluded from release APKs. Device tests verify native
recognition/HNSW, the authenticated complete backend, GPS/date map data, imports,
people/review, renaming, settings and database round-trip, plus the actual React
WebView navigation. Gradle stores the reports under `android/app/build/reports/`. The existing
`scripts/check-ui.sh` also exercises map filters, races and all five pages at 390px.

For physical S22 acceptance, additionally verify One UI storage permission and
original-photo GPS access, long background imports, representative personal photo
quality, touch map selection in portrait/landscape and update installation.

## Signing local packages

Root `VERSION` controls the visible version and Android version code. Use `scripts/sign-android.sh` to sign an APK for local installation.

Local signing uses `ANDROID_KEYSTORE_PATH`, `ANDROID_KEYSTORE_PASSWORD`,
`ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` and `ANDROID_HOME`. Keep the key and
credentials outside the repository, back them up privately and reuse the same
identity for future updates. Never upload the key/backup as a release asset. Losing
the key prevents installing future updates over the old app while keeping its
private library. A locally signed APK has a checksum but no GitHub attestation
for the final signing step.
