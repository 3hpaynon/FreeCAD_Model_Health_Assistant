# Model Health Assistant Architecture

Model Health Assistant is a local-first FreeCAD workbench with an optional,
explicitly consented AI explanation branch. Deterministic extraction, risk
rules, scoring, and local reporting remain independent of provider packages,
credentials, and network availability.

## End-to-End System Flow

```text
FreeCAD document
  -> local extractor
  -> immutable-style Python fact snapshot
  -> deterministic risk engine and health score
  -> ModelHealthReport (report schema 0.3)
     -> typed local JSON writer
     -> UI-neutral analysis presentation
        -> FreeCAD Report View and results dialog
     -> explicit user AI action
        -> allowlisted AIRequestContext (schema 1.0)
        -> deterministic size/truncation controls
        -> versioned provider-neutral request (schema 1.0)
        -> final payload preview and one-request consent
        -> request-time provider/credential snapshot
        -> asynchronous provider-neutral execution
           -> LocalMockAIProvider, or
           -> OpenAI adapter
              -> short-lived client and secure credential retrieval
              -> Responses API, strict structured output, tools disabled
        -> provider-neutral AIExplanationResponse (schema 1.0)
        -> response validation and source-warning grounding
        -> completion-time report/selection/fingerprint validation
        -> read-only traceable explanation display
```

The OpenAI route never receives a live FreeCAD document, report file/path, or
credential through the provider-neutral request.

## Local Analysis Branch

### FreeCAD entry points

FreeCAD discovers the addon through root-level `Init.py` and `InitGui.py`.
`Init.py` resolves the addon root and exposes `src` on `sys.path`. `InitGui.py`
registers `ModelHealthAssistantWorkbench`, the stable analysis command
`MHA_TestCommand`, and the provider-settings command `MHA_ProviderSettings`.

The entry points contain no credential storage, prompt construction, OpenAI SDK
import, or provider call. Optional dependencies are not imported and credentials
are not retrieved during workbench discovery.

### Extraction and report models

`extractor.py` reads the active FreeCAD document defensively and creates pure
Python `FeatureInfo` facts. `models.py` owns `FeatureInfo`, `SketchInfo`,
`DependencyInfo`, `RiskWarning`, and `ModelHealthReport`. No network or AI code
is involved.

The extractor captures structured names, labels, type/category, dependency
relationships, sketch support, and confirmed recompute/error facts. It treats
missing FreeCAD metadata conservatively rather than inventing values.

### Risk engine and score

`risk_engine.py` is the only source of local findings, severity, deduplication,
and score penalties. Formula version `1.0` starts at 100, applies reviewed rule
penalties and a bounded prevalence term, then clamps to 0-100.

| Rule ID | Penalty per affected feature | Rule cap |
| --- | ---: | ---: |
| `confirmed_recompute_error` | 25 | none |
| `sketch_generated_geometry_support` | 8 | 32 |
| `early_finishing_feature` | 5 | 20 |
| `underdefined_sketch` | 4 | 20 |
| `high_downstream_dependency_count` | 2 low / 4 medium | 16 |
| `unknown_sketch_support` | 0 | 0 |
| `hardcoded_repeated_dimensions` | 0 | 0 |

Unrecognized rules have zero score impact until explicitly reviewed. The report
retains the complete score breakdown.

### Typed local report boundary

`report_writer.py` accepts only `ModelHealthReport`; arbitrary dictionaries are
rejected before a file is created. It writes local report schema `0.3`. Provider
settings, credentials, AI context, prompts, responses, usage, and report paths
are not fields of `ModelHealthReport` and cannot enter normal report
serialization through the writer.

The report contains structured model facts but no raw geometry. Legacy
`document_name`, `timestamp`, and `total_feature_count` aliases remain for report
consumers.

### Local presentation

`presentation.py` converts a completed report into `AnalysisPresentation` and
`WarningDisplayItem` without rerunning analysis or mutating the report.
`ui.py` owns the native results dialog, feature selection, report-folder/path
actions, and manual refresh. Feature selection resolves the analyzed document
and structured internal object name before using `FreeCADGui.Selection`; it does
not change model properties.

## Optional Dependency Boundary

`runtime_dependencies.py` obtains FreeCAD's platform-correct user-data
directory from `FreeCAD.getUserAppDataDir()` when running inside FreeCAD, then
calculates and lazily appends an existing user-owned ABI-specific directory:

```text
<FreeCAD user directory>/ModelHealthAssistantDependencies/py<major><minor>-<platform>_<architecture>
```

Windows retains the `%APPDATA%\FreeCAD` fallback when FreeCAD's module is not
available. macOS does not guess a `~/Library` location; FreeCAD's reported user
directory is authoritative. Current directory examples include
`py311-win_amd64`, `py311-macos_arm64`, and `py311-macos_x86_64`.

It never creates the directory, installs packages, prepends it ahead of
FreeCAD's built-ins, or modifies the FreeCAD installation. The pinned optional
dependency set is `openai==2.45.0` and `keyring==25.7.0`.

`ai_capabilities.py` reports missing SDK/package/backend states without importing
optional dependencies during workbench discovery. Missing or broken real-AI
dependencies do not disable local analysis or the mock provider.

## Credential Boundary

`credentials.py` defines the provider-neutral credential protocol and
`ProviderCredentialStore`. Stable identifiers are:

- application: `freecad-model-health-assistant`
- provider: `openai`
- profile: `default`
- service: `freecad-model-health-assistant:provider-credential`
- account: `openai:default`

`keyring_backend.py` lazily imports `keyring`, requires a positive-priority
allowlisted operating-system backend, and rejects null, fail, chainer,
plaintext, and file backends. Backend exceptions are translated into fixed
project errors without their raw text.

The key can be read only through
`retrieve_for_provider_execution("openai")`. Status types report configured,
missing, unavailable, or storage-error state without returning the key. No
credential-bearing dataclass, serialized status, plaintext fallback, preference
field, repository file, or environment fallback exists.

`provider_settings_ui.py` owns the hidden key input and explicit save, replace,
remove, and connection-test controls. The stored key is never read back into the
UI. `provider_settings.py` and `freecad_provider_settings.py` persist only these
non-secret fields:

- `provider_identifier`
- `preferred_model_identifier`
- `request_timeout_seconds`
- `maximum_retries`
- `maximum_output_tokens`
- `reasoning_effort`
- `response_verbosity`
- `real_provider_enabled`
- `last_explanation_mode`

## Privacy-Safe Context Boundary

`ai_context.py` is the only report-to-provider data boundary. It creates a new
`AIRequestContext` from explicit fields and allowlisted evidence. It does not
copy the report object or parse the saved JSON file.

Excluded inputs include raw CAD files, geometry, meshes, point clouds, images,
screenshots, arbitrary custom properties, environment variables, credentials,
console output, stack traces, and local paths. The real document name is
excluded by default. Absolute Windows and private POSIX paths are rejected or
redacted.

Default deterministic policy `context-limits-v1` applies:

| Limit | Default |
| --- | ---: |
| Included warnings | 50 |
| Inline feature summaries | 50 |
| Characters per text field | 2,000 |
| Items per allowlisted evidence list | 10 |
| Canonical serialized context characters | 64,000 |

Warnings are prioritized high, medium, then low, with stable source order within
each severity. Metadata discloses original/included/omitted warning counts,
truncation, omitted field identifiers, configured limits, and serialized size.
These are character/item limits, not token estimates.

Selected-warning requests build a one-warning scoped report snapshot, then
restore only the global counts needed for context. Full analysis and grounded
questions use the complete limited allowlisted context.

## Prompt and Consent Boundary

`ai_prompts.py` serializes CAD-derived text as canonical JSON between explicit
untrusted-data delimiters. The system instruction says that labels, warning
text, evidence, and similar CAD-originated strings are data rather than
instructions. A grounded user's question is a separate bounded field and
separate delimiter block.

Versions are:

- explanation prompt: `model-health-explanation-v3`
- grounded-question prompt: `model-health-grounded-question-v2`
- context schema: `1.0`
- request schema: `1.0`
- response schema: `1.0`

`ai_consent.py` freezes `AIRequestReview`, containing provider, configured model,
request type, the exact provider-neutral request, the source report fingerprint,
and SHA-256 request fingerprint. The preview displays both the privacy-safe
context and exact provider-neutral request. `AIRequestConsent` is ephemeral,
must be approved through **Send to Provider**, can be consumed once, and is
invalidated by any report, selection, request, provider, or model change.

No blanket or persistent consent exists.

## Provider-Neutral Workflow

`ai_workflow.py` resolves current non-secret settings and readiness. For a real
request, it verifies the static model capability record and secure credential
status without fetching the key. After consent is consumed on the GUI thread,
it builds `AIExecutionPlan` containing only:

- request type
- provider identifier
- configured model identifier
- reviewed `AIExplanationRequest`
- selected provider implementation

The plan contains no FreeCAD document, Qt widget, report path, settings adapter,
or raw report. Credential retrieval occurs inside provider client construction
when the plan actually executes.

`AIExplanationService` invokes the injected `AIExplanationProvider` and validates
the provider-neutral response. `LocalMockAIProvider` is deterministic, offline,
and credential-free. It passes through the same response validation contract.

## OpenAI Adapter

`openai_provider.py` is isolated from FreeCAD, Qt, reports, rules, scoring, and
credential backend implementation. It lazily imports the official SDK and
constructs a short-lived client only for an explicitly launched request.

The adapter uses the Responses API with:

- strict JSON-schema structured output
- `store=False`
- `background=False`
- `stream=False`
- `tools=[]`
- `tool_choice="none"`
- `parallel_tool_calls=False`
- bounded SDK retries and explicit timeouts

It does not upload files, enable tools, perform web/file search, execute code,
use computer control, create persistent conversations, or retain response IDs
for continuation.

Configured defaults are:

- provider: `openai`
- model: `gpt-5.6-terra`
- connect timeout: 10 seconds
- read timeout: 45 seconds
- total request timeout: 60 seconds
- SDK maximum retries: 2
- maximum output: 8,192 tokens
- reasoning effort: `low`
- response verbosity: `medium`

The static registry also supports `gpt-5.6-luna` and `gpt-5.6-sol`. There is no
startup model-list fetch and no automatic model switching.

The adapter converts SDK responses immediately to provider-neutral dataclasses.
Raw SDK response objects, headers, and provider exception text do not cross the
adapter boundary. It retains only safe metadata: provider, actual model when
available, request ID, and provider-reported token counters.

## Response Validation and Traceability

`ai_service.py` rejects wrong response types/schema/provider identity, malformed
usage, missing required text/limitations, and warning explanations that do not
match one source tuple:

```text
(warning_id, rule_id, affected_feature_internal_name)
```

It also rejects duplicate identities and source-severity mismatches. A valid
partial response may omit some per-warning explanations; omission never creates
a local finding.

`ai_traceability.py` repeats grounding at UI completion, revalidates the report,
selection, and request fingerprint, and produces immutable
`AIResponseTraceability` and `AIWarningTraceability` records. These retain source
rule ID, feature internal name/label, source severity, request fingerprint,
response schema, provider, configured/actual model, prompt version, and context
version. The original `RiskWarning` objects are never modified.

`ai_result_presentation.py` labels local findings and AI-generated advisory text
separately. It displays provenance, provider/model/request/usage metadata,
limitations, and the no-model-change disclaimer. Output is plain read-only text;
there are no executable suggestion actions.

## Asynchronous State and Cancellation

`qt_async.py` runs a prepared provider operation away from the GUI thread and
returns results through Qt signals. The worker receives no live FreeCAD object.
UI updates occur only in main-thread callbacks.

`ai_request_state.py` allows one accepted request per interface and issues a
monotonic ticket. Progress, success, and failure callbacks must present the
active ticket. Cancellation is logical: the ticket is invalidated and late
callbacks are ignored while the underlying SDK call may end normally. Threads
are never forcibly terminated. Detach/close states suppress callbacks to closed
or destroyed dialogs.

The results callback performs traceability validation against the controller's
current report and, for a selected request, its current warning selection and
live feature target. A response that became stale while the provider was
running is rejected rather than displayed.

## Errors, Retries, and Diagnostics

`ai_provider.py` defines provider-neutral errors for missing credentials,
credential backend failure, dependency failure, authentication, permission,
unsupported model, invalid request, rate limit, connection, timeout, server
failure, refusal, output limit, malformed output, cancellation, and unknown
provider failure.

`ai_error_handling.py` maps those types to fixed user-facing categories without
using raw exception text. Report View diagnostics contain only redacted request
type/provider/model/category/timing metadata. They exclude credentials,
authorization headers, full context/responses, stack traces, and local paths.

The OpenAI SDK owns the single bounded retry policy. The application does not
add another loop. Authentication, permission, invalid request/model, refusal,
and malformed responses are non-retryable. No request is retried indefinitely.

## Responsibility Map

- `Init.py`: expose local package layout.
- `InitGui.py`: register workbench and commands; delegate activation.
- `extractor.py`: read FreeCAD into a defensive fact snapshot.
- `models.py`: define local facts, warnings, and report schema.
- `risk_engine.py`: own local rules, severity, deduplication, and score.
- `report_writer.py`: write only typed local reports.
- `presentation.py`: create UI-neutral local display data.
- `runtime_dependencies.py` / `ai_capabilities.py`: locate and report optional
  runtime capability without installation or network calls.
- `credentials.py` / `keyring_backend.py`: isolate provider-neutral and OS-backed
  secret storage.
- `provider_settings.py` / `freecad_provider_settings.py`: persist allowlisted
  non-secret settings.
- `provider_settings_ui.py`: key management and explicit connection test.
- `ai_models.py`: provider-neutral request/response contracts.
- `ai_context.py`: privacy allowlist and deterministic context limits.
- `ai_prompts.py`: untrusted-data delimiters and versioned prompts.
- `ai_consent.py`: exact preview, fingerprints, and one-use consent.
- `ai_model_capabilities.py`: static provider/model capability validation.
- `ai_provider.py`: provider protocol and neutral error taxonomy.
- `mock_ai_provider.py`: deterministic offline provider.
- `openai_provider.py`: Responses API translation and SDK error mapping.
- `ai_service.py`: provider execution and structured-response validation.
- `ai_workflow.py`: readiness, review, provider selection, and execution plan.
- `ai_request_state.py` / `qt_async.py`: request tickets, background execution,
  logical cancellation, and stale-callback rejection.
- `ai_traceability.py`: completion-time grounding and immutable provenance.
- `ai_result_presentation.py` / `ai_usage_presentation.py`: read-only advisory
  text and safe provider/model/request/token metadata.
- `ui.py`: results workflow, consent launch, completion revalidation, and dialogs.

## Security Invariants

- Workbench startup performs no provider import, key retrieval, or network call.
- Missing optional dependencies, missing keys, or offline state do not disable
  local analysis.
- Secrets are stored only through an approved OS credential backend; failures
  are closed with no plaintext fallback.
- Settings and report writers use explicit non-secret/typed schemas.
- Every real request is explicit; every explanation/question receives exact
  payload review and one-use consent.
- Previewed context is the same context object passed to provider execution.
- Provider tools, uploads, code execution, and model mutation are unavailable.
- Responses cannot become local rule findings or execute application actions.
- Unknown, duplicate, mismatched, malformed, or stale response data is rejected.
- Automated tests use fake providers/backends and obvious sentinel values; they
  never access the real credential store or make a live provider call.
