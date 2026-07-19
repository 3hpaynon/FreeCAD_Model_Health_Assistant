# Model Health Assistant

Model Health Assistant is a local-first FreeCAD workbench for inspecting a
parametric model's feature tree, identifying potentially fragile modeling
patterns, and producing a local JSON model-health report. Its optional AI layer
can explain that completed rule-based analysis through either a deterministic
offline mock provider or OpenAI using a key supplied by the user.

The local analyzer remains the source of every warning and health score. AI
output is read-only advisory text: it does not create findings, inspect unseen
geometry, execute code, edit the FreeCAD document, or guarantee a repair.
No automatic CAD modification is available.

## Capabilities

- Extract FreeCAD objects, categories, sketch support, dependencies, and
  confirmed error state into pure Python models.
- Run deterministic risk rules and a transparent health-score calculation.
- Write a local JSON report using report schema `0.3`.
- Display summary counts, warning details, feature selection, report-folder
  actions, and manual refresh in a reusable FreeCAD results dialog.
- Preview the exact privacy-safe context before any real provider request.
- Store a user-supplied OpenAI key through an approved operating-system
  credential backend, with explicit save, replace, and remove operations.
- Run an explicit, minimal **Test Connection** request containing no CAD data.
- Explain one selected warning, explain the complete included analysis, or ask
  one grounded question about the current analysis.
- Run provider calls off the GUI thread with progress, bounded timeouts/retries,
  logical cancellation, and rejection of late or stale results.
- Validate structured provider responses and retain source-warning and request
  traceability in the explanation display.
- Continue all local analysis and mock-provider functionality without an API
  key, network connection, OpenAI SDK, or credential package.

## Safety and Cost Boundaries

This is a bring-your-own-key desktop integration. The project ships no shared
or developer API key. OpenAI usage is billed to the account associated with the
key and may incur charges. Review the provider's current model and usage terms
before sending requests.

Never commit an API key to Git, place one in source code or a report, or share
one in a screenshot or support message. Documentation uses only the placeholder
`YOUR_API_KEY`. The addon stores a key through the operating-system credential
backend; it does not store the key in the repository, JSON reports, FreeCAD
preferences, provider settings, logs, prompts, or AI context.

No request occurs at FreeCAD startup, when the workbench is selected, when the
settings dialog opens, when a key is saved, or when local analysis runs. Every
real request requires an explicit user action. Explanation and question
requests additionally require review of the final payload followed by
**Send to Provider**.

## Install the FreeCAD Workbench

1. Make this repository available under the user's FreeCAD `Mod` directory by
   using the existing addon-directory or linked-directory setup.
2. Fully restart FreeCAD.
3. Choose **Model Health Assistant** in the workbench selector.
4. Open or activate a document and click **Analyze Model Health**.

`Init.py` exposes the repository's `src` directory. `InitGui.py` registers the
workbench and commands but does not import the OpenAI SDK, retrieve a credential,
or perform network activity during discovery.

## Optional Runtime Dependencies

Real OpenAI use requires these pinned optional dependencies:

- `openai==2.45.0`
- `keyring==25.7.0`

The repository `.venv` and FreeCAD's embedded Python are separate runtimes.
Installing a package into one does not make it importable by the other.

### Development virtual environment

The normal automated suite needs only the development dependencies. To exercise
the optional imports in the repository virtual environment, install the
`real-ai` extra into the existing `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[real-ai]"
```

Do not create a second project virtual environment.

### FreeCAD embedded runtime on Windows

First inspect FreeCAD's runtime in **View > Panels > Python console**:

```python
import platform
import site
import sys

print(sys.version)
print(platform.machine())
print(site.ENABLE_USER_SITE)
print("\n".join(sys.path))
```

The addon looks for optional packages in a dedicated user-writable directory:

```text
%APPDATA%\FreeCAD\ModelHealthAssistantDependencies\py<major><minor>-<architecture>
```

For 64-bit FreeCAD using Python 3.11, that directory is:

```text
%APPDATA%\FreeCAD\ModelHealthAssistantDependencies\py311-win_amd64
```

Use a standalone Python interpreter matching FreeCAD's Python major/minor and
architecture. For the Python 3.11 example:

```powershell
$mhaRuntimeTarget = Join-Path $env:APPDATA "FreeCAD\ModelHealthAssistantDependencies\py311-win_amd64"
py -3.11 -m pip install --upgrade --target $mhaRuntimeTarget "openai==2.45.0" "keyring==25.7.0"
```

Restart FreeCAD after installation. This target is outside both the repository
and the FreeCAD installation. The addon appends the existing ABI-specific
directory to `sys.path` lazily; it never creates the directory or runs `pip`.
Do not install into or modify FreeCAD's installation directory.

To update dependencies, rerun the same command with the reviewed pinned
versions. To remove the optional runtime dependencies, close FreeCAD and remove
only that exact ABI-specific `ModelHealthAssistantDependencies` directory. This
does not remove a credential from the operating-system credential backend; use
**Remove Key** separately.

## Configure OpenAI

OpenAI's official [developer quickstart](https://developers.openai.com/api/docs/quickstart)
links to the API dashboard and documents the official Python SDK and Responses
API. Create and manage keys in the provider dashboard; do not paste a key into
the terminal for this addon.

1. In FreeCAD, open **Model Health Assistant > AI Provider Settings**.
2. Choose **OpenAI**.
3. Choose one of the locally registered model IDs:
   `gpt-5.6-luna`, `gpt-5.6-terra`, or `gpt-5.6-sol`.
   `gpt-5.6-terra` is the default balance of capability and cost.
4. Review the timeout, retry, and maximum-output settings and click
   **Save Settings**.
5. Paste your newly created key into the hidden **API key** field and click
   **Save Key**. The field clears after successful storage.
6. Click **Test Connection**, review the confirmation, and approve the minimal
   real request if desired. No CAD data, report, document name, feature label,
   file, or geometry is included.

The key field is empty whenever the dialog opens. A stored key is never read
back into the field, displayed in masked form, copied to the clipboard, placed
in a tooltip, or included in status text.

Use **Replace Key** to replace an existing credential explicitly. Use
**Remove Key** and approve its confirmation to delete the credential from the
secure backend. The stable credential identifiers are:

- service: `freecad-model-health-assistant:provider-credential`
- account: `openai:default`

On Windows, `keyring` must resolve to its approved Windows credential backend.
The addon fails closed if the package or secure OS backend is unavailable; it
does not fall back to a plaintext file, environment variable, or FreeCAD
preference.

## Use the Analysis and AI Actions

Click **Analyze Model Health** first. The command reads the active document,
runs local rules, prints a summary to Report View, writes a JSON report when
possible, and opens or updates **Model Health Assistant Results**.

The results dialog provides these AI-related actions:

- **Preview AI Context** shows the locally built allowlisted context. It never
  sends data.
- **Test Mock Explanation** runs the deterministic local mock path without an
  API key or network request.
- **Explain Selected Warning** sends only the selected warning plus minimal
  report-level context.
- **Explain Full Analysis** sends the warnings retained after the deterministic
  context limits and discloses any omitted or truncated material.
- **Ask About This Analysis** sends one bounded question separately from the
  untrusted CAD-derived context. It has no persistent chat history and must say
  when the supplied report is insufficient.

For a real explanation or question:

1. Invoke the desired action.
2. Review provider, configured model, request type, schema/prompt versions,
   warning counts, truncation state, question state, request fingerprint, exact
   privacy-safe context, and exact provider-neutral request.
3. Click **Cancel** to send nothing, or **Send to Provider** for that one request.
4. Review the read-only result. Local findings and AI-generated explanations
   are shown separately with provider/model/request/usage metadata and an
   advisory disclaimer.

Consent is not persisted. A selected-warning review cannot authorize a full
analysis, and every full analysis or question receives a fresh review. If the
report, selection, provider, model, or reviewed payload changes, consent is
invalidated. The same checks run again when a provider response completes, so a
late response cannot be displayed against a changed report or warning.

## Information Sent and Excluded

The real provider receives only the exact privacy-safe context shown in the
final review, the versioned system instruction, and an optional separately
delimited user question.

Allowlisted context can include:

- health score and transparent score summary
- document-object, meaningful-feature, sketch, category, and severity counts
- included warning IDs, rule IDs, severity, messages, technical reasons, and
  suggested practices
- affected feature internal name, display label, category/type, and a limited
  set of support/dependency facts
- analysis limitations and deterministic truncation metadata

It excludes:

- `.FCStd` files, raw JSON reports, file uploads, B-rep geometry, meshes, point
  clouds, screenshots, and images
- document, report, repository, home-directory, and FreeCAD installation paths
- raw FreeCAD/Qt objects, arbitrary custom properties, environment variables,
  console output, stack traces, and credentials
- provider tools, web search, file search, code execution, computer use,
  background mode, streaming, and persistent conversations

CAD-originated labels and warning text are treated as untrusted data inside
explicit JSON delimiters. Text that resembles an instruction remains data and
cannot enable provider tools or change the application's behavior.

## Structured Responses and Traceability

The OpenAI adapter uses the Responses API with strict structured output and
`store=False`. Context, request, and response schemas are version `1.0`.
Explanation prompt template `model-health-explanation-v3` and grounded-question
template `model-health-grounded-question-v2` are recorded in request metadata.

Before display, each AI warning explanation must map to one exact source tuple:
warning ID, rule ID, affected feature internal name, and severity. Unknown
warnings, wrong feature targets, duplicates, severity mismatches, malformed
output, stale fingerprints, and changed reports/selections are rejected. Valid
partial responses may omit an explanation, but omissions are stated explicitly.

The result retains or displays the request context fingerprint, response schema,
prompt and context versions, source feature label/internal name, source severity,
provider, configured model, actual model when returned, request ID, and
provider-reported token usage when available. Token counts are never invented,
and the addon does not calculate price.

## Timeouts, Retries, and Cancellation

Defaults are a 60-second request timeout, 10-second connection timeout,
45-second read timeout, two maximum retries, and 8,192 maximum output tokens.
Retries are bounded and owned only by the OpenAI SDK; the application does not
add a second retry loop. Authentication and permission failures are not retried.
The configured model is never changed automatically after a failure.

Only one provider request may run at a time per results interface. Network work
runs outside the GUI thread. **Cancel Request** performs logical cancellation:
it immediately restores the UI and ignores the late result, while allowing an
already-running SDK call to finish safely. Python threads are never forcibly
terminated. Closing a dialog detaches callbacks so deleted widgets are not
updated.

## Troubleshooting

### OpenAI SDK dependency is unavailable

- Confirm the package was installed for FreeCAD's embedded Python ABI, not only
  into `.venv`.
- In FreeCAD's Python console, inspect `sys.version`, `platform.machine()`, and
  `sys.path`.
- Confirm the ABI-specific directory exists under
  `%APPDATA%\FreeCAD\ModelHealthAssistantDependencies` and contains the pinned
  packages, then restart FreeCAD.
- A broken optional dependency must not prevent the workbench or local analyzer
  from loading.

### Secure credential storage unavailable or backend error

- Confirm `keyring==25.7.0` is importable in FreeCAD's runtime.
- Confirm Windows Credential Manager is available and the selected keyring
  backend is the approved Windows OS backend, not a plaintext/file backend.
- Remove and reinstall only the optional ABI-specific dependency folder if its
  installation is corrupt. The addon never creates an insecure fallback.

### Authentication or permission failure

- Confirm the key is active and belongs to the intended OpenAI project/account.
- Confirm that its permissions allow the selected model and Responses API.
- Replace the stored key explicitly; the addon will not silently reuse text from
  the empty key field or switch models.

### Unsupported model

Choose one of the three model IDs registered in the settings dialog. The model
registry is local and network-free; no model list is fetched at startup. A
provider-side unsupported-model response is shown as an error and does not
change the configured choice.

### Rate limit, quota, timeout, or connection failure

Rate-limit and quota responses are provider/account conditions. Wait or review
the provider account's limits and billing. For a timeout or offline failure,
check connectivity and the configured timeout before retrying. Provider server
errors and bounded retryable failures may be retried by the SDK. No request is
retried indefinitely.

### Output limit or malformed structured response

If the provider stops at the configured output limit, increase **Maximum
output** and submit a newly reviewed request. Malformed, incomplete, refused,
or ungrounded output is never displayed as a successful explanation.

### FreeCAD startup issue

Remove only the optional ABI-specific dependency directory temporarily and
restart FreeCAD. The workbench should still load and local analysis/mock use
should remain available. Review Report View for the error category; do not share
credentials, complete stack traces, or private paths.

## Rotate or Remove a Key

If a key may be compromised, revoke/delete it in the OpenAI dashboard first,
review account usage, create a replacement, then use **Replace Key** and
**Test Connection** in FreeCAD. OpenAI's
[API key safety guidance](https://help.openai.com/en/articles/5112595)
recommends immediate rotation after suspected exposure and never committing keys
to a repository.

For normal removal, use **Remove Key** in Provider Settings. If the addon cannot
open, remove the exact `openai:default` entry under the
`freecad-model-health-assistant:provider-credential` service using the operating
system's credential-management interface. Removing the addon or Python packages
alone does not guarantee that the OS credential entry was removed.

## Known Limitations

- Analysis is a manual snapshot and sees only facts exposed by FreeCAD and the
  extractor.
- Rule warnings and the health score are engineering aids, not proof of failure
  or correctness.
- AI explanations are limited to the reviewed context and may be incomplete or
  incorrect; no geometry is sent or independently inspected.
- Logical cancellation cannot guarantee that a provider stops processing an
  SDK call already in progress; it guarantees only that the late result is not
  accepted by the UI.
- The static model registry must be updated deliberately as provider
  capabilities change. The addon does not auto-discover models.
- No pricing table or cost calculator is maintained.
- There is no persistent AI conversation, telemetry, analytics, cloud sync,
  autonomous repair, or CAD generation.

## Development and Verification

Use the existing repository virtual environment:

```powershell
.\.venv\Scripts\python.exe -m compileall Init.py InitGui.py src tests
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.\.pytest_temp
git diff --check
```

Normal automated tests use fakes and sentinels. They do not access the real OS
credential entry or make a live API request. A live test is performed only by
the user through FreeCAD after explicitly choosing **Test Connection** or
reviewing and sending an explanation request.

See [docs/architecture.md](docs/architecture.md) for component boundaries and
the complete request path.
