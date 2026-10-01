# Face Manager for Android

The standalone Android APK runs the same React interface, FastAPI services and
SQLite library workflows as the PC application on the phone. This includes the
world map, images and folder filters, face review and suggestions, people,
filename previews/renaming, background tasks, settings, and database backup and
restore. No running PC or hosted photo service is required.

## Install on a Samsung Galaxy S22 (Android 16 / One UI 8.0)

1. Download `FaceManager-Android-X.Y.Z.apk` from the GitHub Release onto the phone.
2. Open it and allow your browser or file manager to install this app when Android
   asks. If Samsung Auto Blocker prevents installation, temporarily turn it off in
   **Settings → Security and privacy → Auto Blocker**, then restore it afterwards.
   Menu labels vary by One UI version.
3. Open **Face Manager**. Grant **All files access** on the Android settings screen
   opened by the app. The app needs actual photo-folder access for the PC workflows,
   including recursive imports, duplicate locations and filename changes.
4. Allow access to stored photo-location metadata when asked so GPS photos appear
   on **Weltkarte**. This reads existing photo metadata, not your live location.
5. Choose **Ordner hinzufügen** and select a folder such as
   `/storage/emulated/0/DCIM/Camera`. The original files remain there.
6. Review faces, name people and use the same sidebar views as on the PC.

Android 11/API 30 or newer is required. The universal APK supports ARM64 (both S22
processor variants) and x86-64. CI runs device tests on Android 12/API 31, Android
15/API 35 and Android 16/API 36. These emulator checks do not constitute a physical
Galaxy S22 or Samsung One UI test.

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
Android's own package installer handles update confirmation; Windows installers
and CUDA acceleration naturally use the corresponding Android package and CPU.

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

CI runs JVM tests, Android lint, builds, and isolated instrumentation on API 31/35/36.
Android Test Orchestrator clears each test's application data. Debug builds alone
include the shared backend regression tests and test helpers. The real-person
fixtures and sources are documented under `app/src/androidTest/assets/README.md`;
tests/helpers/fixtures are excluded from release APKs. Device tests verify native
recognition/HNSW, the authenticated complete backend, GPS/date map data, imports,
people/review, renaming, settings and database round-trip, plus the actual React
WebView navigation. Reports are retained as Actions artifacts. The existing
`scripts/check-ui.sh` also exercises map filters, races and all five pages at 390px.

For physical S22 acceptance, additionally verify One UI storage permission and
original-photo GPS access, long background imports, representative personal photo
quality, touch map selection in portrait/landscape and update installation.

## Release signing

Root `VERSION` controls the visible version and Android version code. Use the
repository release helper. Administrators can configure persistent Actions secrets:
`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, and
`ANDROID_KEY_PASSWORD`. Never use the development debug key for releases.

The release workflow builds the exact successful CI commit. With signing secrets,
it verifies/publishes `FaceManager-Android-X.Y.Z.apk` and `.apk.sha256` and creates
build provenance. Otherwise it retains an unsigned workflow artifact for 14 days;
that artifact is not installable. Sign that exact artifact with
`scripts/sign-android.sh` and attach APK/checksum to the release created by CI.
Do not create or replace a tag manually.

Local signing uses `ANDROID_KEYSTORE_PATH`, `ANDROID_KEYSTORE_PASSWORD`,
`ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` and `ANDROID_HOME`. Keep the key and
credentials outside the repository, back them up privately and reuse the same
identity for future updates. Never upload the key/backup as a release asset. Losing
the key prevents installing future updates over the old app while keeping its
private library. A locally signed APK has a checksum but no GitHub attestation
for the final signing step.
