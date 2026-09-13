# Android identity and release signing

The permanent `applicationId`, namespace and activity package are
`com.smply.app`. Keep future Play/Google/Firebase Android registrations aligned
with this ID. This change does not configure those providers or change Apple IDs.

## 1. Create the private release/upload key

No ecomsbd release keystore is configured in this checkout. If you already have
its production key elsewhere, use it instead of generating a replacement.
Otherwise run this in **PowerShell**, with `keytool` from your JDK on PATH:

```powershell
keytool -genkeypair -v -keystore "$env:USERPROFILE\ecomsbd-upload.jks" -storetype JKS -keyalg RSA -keysize 2048 -validity 10000 -alias ecomsbd-upload
```

The command prompts you for passwords and certificate details; no password is
provided by the project or placed in shell history. Do not overwrite an existing
keystore. Keep the key outside the repository, make an encrypted backup and save
the passwords in your password manager. The alias above is an example you may
change; Gradle reads your actual alias from private configuration.

## 2. Configure signing locally or through CI secrets

From `apps/mobile_flutter/android`, copy the blank template **only if you do not
already have `key.properties`**:

```powershell
if (Test-Path -LiteralPath key.properties) { throw 'key.properties already exists; edit it instead.' }
Copy-Item -LiteralPath key.properties.example -Destination key.properties
```

Fill these four values in that ignored file:

- `storeFile`: absolute keystore path, such as
  `C:/Users/YourName/ecomsbd-upload.jks` (forward slashes avoid Java-properties escapes).
- `storePassword`: the keystore password you chose.
- `keyAlias`: the alias you chose (`ecomsbd-upload` if using the command above).
- `keyPassword`: that key's password, including when it equals the store password.

For CI, inject secrets as environment variables instead of writing this file:

| Local property | Environment override |
|---|---|
| `storeFile` | `ECOMSBD_RELEASE_STORE_FILE` |
| `storePassword` | `ECOMSBD_RELEASE_STORE_PASSWORD` |
| `keyAlias` | `ECOMSBD_RELEASE_KEY_ALIAS` |
| `keyPassword` | `ECOMSBD_RELEASE_KEY_PASSWORD` |

Nonblank environment values take precedence per field. Relative keystore paths
resolve against the Android project directory; absolute paths are recommended.
Do not put signing secrets in Dart defines, command-line arguments, tracked
Gradle properties or build logs. `key.properties`, `*.jks` and `*.keystore` are
ignored repository-wide; only the empty example is committed.

Debug signing is unchanged and needs none of these values. Release APK/AAB tasks
use only the `release` signing config. Missing configuration/files or the standard
Android debug key fail the release guard rather than producing an unsigned or
debug-signed release. Minification/resource-shrinking settings are unchanged.

Cheap configuration checks from `apps/mobile_flutter/android`:

```powershell
.\gradlew.bat :app:preDebugBuild --configure-on-demand --console=plain
.\gradlew.bat :app:validateReleaseSigning --configure-on-demand --console=plain
```

The second command is expected to fail until your key/config exists. It checks
configuration only; a subsequent real signed build validates the key/passwords:
`flutter build appbundle --release --dart-define-from-file=env/production.json`
from `apps/mobile_flutter` (with your non-secret release build settings).
Configuration-on-demand keeps these checks scoped to the app rather than
resolving every plugin's build dependencies. Add `--offline` when using cached
Gradle/Android tooling. These checks are not a replacement for a signed build
after the key is supplied.

## 3. Obtain SHA-1 and SHA-256

This command prompts for the store password and prints **both** fingerprints:

```powershell
keytool -list -v -keystore "$env:USERPROFILE\ecomsbd-upload.jks" -alias ecomsbd-upload
```

Use the `SHA1` and `SHA256` certificate fingerprint lines, adjusting the path and
alias if you chose different values. With **Play App Signing**, this is the local
release/upload certificate, not necessarily the certificate on Play-installed
APKs. Obtain the **app signing key certificate** fingerprints from Play Console
→ App integrity as well before configuring Google/Firebase for Play distribution.
Do not use debug-key fingerprints for production builds.

References: [Flutter Android signing](https://docs.flutter.dev/deployment/android#sign-the-app)
and [Android app/upload signing keys](https://developer.android.com/studio/publish/app-signing).
