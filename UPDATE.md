# HmIP-HAP JS1 system button illumination

## Purpose

Expose channel 0 system button illumination as a pure on/off Home Assistant light.

## Problem

The reported device has a writable MASTER float named `SIGNAL_BRIGHTNESS` with
range 0.0–1.0. Its configuration value was available through the Device
Configuration panel but had no dedicated light entity. A generic number or dimmer
would expose brightness controls that are outside the requested behavior.

## Technical Background

The checkout started clean on `main` at
`d09c0426816b88d61e560590783d1150ae2d040a`. Work is on the local branch
`claude/claude-md-hmip-hap-system-button-light`, created from that commit.
All changes remain unstaged and uncommitted. No fetch, pull, push, merge, rebase,
tag, PR, hook installation or Git configuration change was performed.

The implementation was based on the local development checkouts, not on the
production installation (homematicip_local 2.11.1, aiohomematic 2026.9.2,
OpenCCU 3.89.11.20260919, HAP firmware 3.0.18).

The local integration is 2.11.2 (unreleased), pins aiohomematic 2026.10.2,
requires Python 3.14 and Home Assistant 2026.9 or newer, and also supports the
separate openccu-loom backend. The library is 2026.10.2. Local historical tags
for the production versions were unavailable, so an exact historical code diff
could not be made without fetching. The local changelogs document relevant
initial-cache and reconnect fixes and the change from pydevccu to godevccu.

Discovery uses device descriptions and cached paramset descriptions. The generic
resolver normally maps a readable/writable FLOAT to a number. MASTER parameters
must also pass visibility rules. Custom profiles combine registered generic data
points and subscribe to their existing state events.

MASTER reads use the device value cache and client `get_paramset`, ending at
XML-RPC `getParamset`. The central bulk VALUES snapshot is not the source of this
MASTER value. Writes use client `put_paramset`, ending at XML-RPC `putParamset`;
there is no `setParamset` call.

## Architecture Decision

Device recognition and MASTER state/command semantics belong in aiohomematic.
Home Assistant adds a separate light wrapper for this data point, using normal
entity identity, device assignment, subscriptions and availability.

The profile is registered for `HmIP-HAP JS1`, channel 0. Because the registry
also supports prefix matching, creation additionally requires the exact model
(case insensitive), channel type `MAINTENANCE`, and a readable/writable
`DpFloat` from MASTER. No serial number participates in recognition.

## Alternatives Considered

- An HA-only special case would duplicate device recognition and bypass the
  backend's data point state and subscription architecture.
- A global mapping of `SIGNAL_BRIGHTNESS` to light would affect unrelated models.
- A dimmer subclass would carry brightness conversions, timers and capabilities
  into a pure on/off device.
- Generic `send_value` applies optimistic state with a confirmation timeout.
  HmIP MASTER does not emit the matching VALUES callback, so this could later
  roll back an accepted write.
- A new polling loop would add permanent traffic for a configuration parameter.
  The existing initialization, configuration refresh and standard reload paths
  are sufficient for the requested implementation.

## Changed Files

- `aiohomematic/const.py`: named parameter, field and profile constants.
- `aiohomematic/model/custom/profile.py`: one hidden MASTER field and profile lookup.
- `aiohomematic/model/custom/light.py`: discovery guard, on/off state, endpoint commands,
  confirmed readback, registry entry.
- `aiohomematic/model/custom/__init__.py`: public export of the new class.
- `aiohomematic/store/visibility/rules.py`: model/channel-specific MASTER admission.
- `tests/contract/test_cdp_validity_contract.py`: required validity fields.
- `tests/contract/test_capability_protocol_contract.py`: account for the new
  on/off-only class, which has no dimmer/color protocol surface.
- `tests/test_model_data_point.py`: required parameter count and explicit
  SIGNAL_BRIGHTNESS inclusion.
- `tests/test_model_system_button_light.py`: device/profile and wire-path coverage.
- `changelog.md`: Unreleased feature note.
- `UPDATE.md`: implementation and validation record.

## Implementation

The new `CustomDpSystemButtonLight` inherits `CustomDataPoint` directly and
uses a declarative `DataPointField` for `DpFloat`.
Its frozen validity field set contains only `SIGNAL_BRIGHTNESS`.
The profile registry creates channel 0 and normal generic subscriptions handle
updates. Commands use the existing MASTER client API, then the generic loader.
The class exposes no brightness conversion or dimmer capability.

## Entity Behavior

| Device value                         | Light state                                |
| ------------------------------------ | ------------------------------------------ |
| 0.0                                  | OFF                                        |
| 1.0                                  | ON                                         |
| 0.5, or another valid positive value | ON                                         |
| Missing/uninitialized value          | unknown                                    |
| Unavailable device/client            | unavailable through the normal entity base |

`turn_on` requests exactly 1.0; `turn_off` requests exactly 0.0.
A confirmed, valid, certain endpoint already equal to the target skips the write
and the command readback. A positive intermediate value is not equivalent to 1.0
for command deduplication: an explicit ON command still writes 1.0.
Initialization and ordinary refresh do not normalize intermediate values.

The HA wrapper advertises only `ColorMode.ONOFF`, supported features 0, and no
brightness, color, effect or transition support. It is a configuration entity,
enabled by default, with translation key `system_button_light`. The names are
"System button" and "Systemtaste". State is not
restored as a fabricated MASTER value.

The integration resolves this label directly through its HA entity translation,
independently of an already localized backend parameter name. The backend model,
parameter names and command semantics did not change for the naming follow-up.
The integration's follow-up passed its complete quality gate with 1,044 tests
and eight existing skips. The new local test ZIP includes the same modified
backend wheel; this documentation is the only additional backend-repository
change for the shorter entity names.

## Backend Interaction

Commands stay inside the existing client layer, including its throttling and
retry policy. The client logs failed or superseded writes and returns an empty
set of sent keys. The custom data point then leaves the confirmed state unchanged
and skips command readback. It does not add an optimistic value.

After a successful write, the existing data point loader performs one direct
MASTER read, bypassing the short-lived value cache. A read failure follows the
existing uncertainty behavior and retains the last confirmed value; it does not
claim that the command has already been confirmed.

## MASTER Paramset Handling

The model-specific visibility rule admits only channel 0
`SIGNAL_BRIGHTNESS` from MASTER. The custom profile hides the underlying
generic number by default. Its `allow_undefined_generic_data_points` setting
preserves the existing diagnostic and other generic data points of the HAP.

The Device Configuration panel remains on the existing configuration coordinator:
raw FLOAT metadata and validation retain arbitrary valid values from 0.0 to 1.0.
A panel write of 0.5 remains 0.5; the light reports ON after refresh. The feature
does not alter panel code or validation.

## State Synchronization

Initialization loads the registered MASTER data point through the existing
device value cache. Changes publish the existing data point state event to the
custom light, then the HA entity subscription.

HmIP MASTER has no direct value push callback in this architecture: XML-RPC
parameter events are routed to VALUES data points. The existing
`CONFIG_PENDING` true-to-false path calls `Device.on_config_changed`,
which reloads channel metadata and MASTER values. Manual device configuration
reload and existing scheduled data refresh can also update the state.
No new polling, timer or synthetic MASTER event was introduced.

After local commands the immediate confirmed readback updates the entity.
External changes are visible after a supported configuration/reload event or
standard refresh, not necessarily immediately after a panel or WebUI write.

## Compatibility

Existing dimmer/color light wrappers and generic number mappings are preserved.
The feature adds one new custom data point type rather than changing existing
light capabilities globally. Standard unique ID generation uses the device
address and channel; no serial literal is included. HA prefixes that ID with the
integration domain and uses its normal device identifier/configuration URL.

This implementation targets direct aiohomematic connections. The current pinned
openccu-loom client/daemon do not provide this new custom data point. No loom twin,
dependency pin or daemon capability is invented here.

## Regression Considerations

The isolation tests reject other models (including the registry's prefix case),
wrong channels, wrong channel types, VALUES-only metadata, missing parameters
and read-only metadata. A second synthetic address proves serial-independent
recognition. Existing diagnostic data points are retained.
Existing dimmer tests and Device Configuration panel tests are part of validation.

## Tests

The new tests extend `FactoryWithClient` using synthetic device and paramset
descriptions. XML-RPC getParamset/putParamset/getParamsetDescription are mocked,
so discovery,
visibility, generic/custom creation, caching and client/backend routing are real.
Coverage includes 0, 1, 0.5, unknown, endpoint deduplication, uncertain cached values,
failed writes, serial independence, isolation, panel validation and refresh
notifications. Additional tests exercise actual CONFIG_PENDING and UN_REACH
callbacks, diagnostic visibility, and readback failure without optimistic state.
The validity and protocol contracts include the new class. The existing
LightDataPointProtocol carries brightness and color methods; the new class
deliberately has no such surface, matching the explicit empty protocol
expectations already used for other simple custom data points.

## Validation

Validation used Python 3.14.4 and the backend's own test dependencies in
`/tmp/hmip-hap-dev`. Commands ran from this repository.

```sh
/tmp/hmip-hap-dev/bin/python -m pytest tests/ -n0 --tb=short --show-capture=no
```

The complete suite passed: **4,171 passed** in 952.50 seconds. Two additional
callback/readback regression cases were added after that run collected its tests.
The final new module passed separately with **26 passed**. All final light tests,
including those two cases, were then exercised together with the existing dimmer
and sound-player LED tests:

```sh
/tmp/hmip-hap-dev/bin/python -m pytest \
  tests/test_model_system_button_light.py tests/test_model_light.py \
  tests/test_model_sound_player.py -n0 -q --tb=short --show-capture=no \
  --cov=aiohomematic.model.custom.light \
  --cov-report=json:/tmp/hmip-hap-light-coverage.json --cov-report=term
```

Result: **59 passed**, light-module coverage **92.33%**, exceeding the unchanged
85% threshold. The new class has all 31 executable lines and all six measured
branches covered. A previous narrower light-only selection had 84.94% coverage;
including the existing LED-light tests supplied the appropriate module coverage.
The coverage threshold was not changed.

```sh
env PATH=/tmp/hmip-hap-dev/bin:/usr/bin:/bin \
  PREK_HOME=/tmp/hmip-hap-prek prek run --all-files
env PATH=/tmp/hmip-hap-dev/bin:/usr/bin:/bin \
  PREK_HOME=/tmp/hmip-hap-prek prek run \
  --files tests/test_model_system_button_light.py UPDATE.md
git diff --check
```

The full repository hook gate passed, including Ruff formatting/lint, Mypy,
Pylint, Bandit, spelling, Prettier and the repository's architectural contracts.
The explicit new-file gate covers files that are not yet tracked. The backend
whitespace check passed. No checks, tests, thresholds or quality configuration
were disabled or relaxed.

The first full test run exposed five new-contract failures: the required
parameter count and the capability-protocol expectations. Those contracts were
updated to describe the new parameter and the actual on/off-only class surface;
the subsequent full suite passed. Test execution uses `-n0` to avoid concurrent
worker memory pressure in this constrained workspace.

## Dependency / Release Considerations

No manifest, runtime version constant, dependency pin or package release version
was changed. The backend changelog uses an Unreleased section; the integration
uses its existing unreleased 2.11.2 section, so published release notes remain
unchanged.

Home Assistant validation uses the local editable aiohomematic and
aiohomematic_test_support packages, installed with:

```sh
uv pip install --python /tmp/hmip-hap-ha/bin/python --no-deps \
  --config-settings editable_mode=compat \
  -e /home/codex/git/ha_hmip/aiohomematic \
  -e /home/codex/git/ha_hmip/aiohomematic/aiohomematic_test_support
```

Before publishing the integration, the backend feature must be released and the
HA runtime/test pins updated to that actual release, with the corresponding
integration release chosen by the maintainer. The currently published backend
2026.10.2 does not contain the new exported class and cannot run the modified HA
light platform without the local checkout.

The setuptools-compatible editable mode also makes the local typed package
discoverable by Mypy. Default editable import hooks produced Any-related errors;
an explicit MYPYPATH made the sources visible but checked unrelated backend
modules under the integration's different Mypy configuration. Compatibility mode
lets both projects pass their own unmodified typing rules.

The task's HA environment resides in the integration's already ignored
`venv/` directory, with its original /tmp path linked to it. This reduces RAM
usage because /tmp is a small tmpfs. Node and GNU make were prepared under /tmp;
the Node archive and the pinned godevccu simulator were checksum-verified.

## Limitations / Open Questions

- Real HAP/OpenCCU hardware was not available for this workspace validation.
  The device metadata and XML-RPC state in new tests are synthetic.
  The user subsequently reported that the local test package works on their HAP
  and creates the light entity. This is a user-reported hardware result, not an
  independently executed full hardware test matrix.
- External MASTER changes depend on existing configuration refresh support.
- A failed readback can leave the previous state marked uncertain until refresh.
- Local historical tags were absent; no Git remote operation was performed to
  reconstruct production code.
- openccu-loom support needs a corresponding daemon/client implementation and
  release outside these two checkouts.
- A release version and the future dependency pin remain maintainer decisions.

## User-visible Behavior

The HAP device gains one configuration light for system button illumination,
with an on/off toggle and no brightness slider. ON selects full illumination;
OFF disables it. A value configured as 0.5 remains editable in the Device
Configuration panel and appears as ON in the light. Unknown and unavailable
states remain distinct from OFF.
