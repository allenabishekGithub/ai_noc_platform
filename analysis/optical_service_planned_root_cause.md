# Optical Service Stuck in PLANNED - Root Cause and Recommended Fix

## Scope
This report summarizes why optical service creation from `tfs-agentic-module` ends in controller status `PLANNED` instead of `ACTIVE`.

## Logs Collected
- `nbi_logs_since_2h.log`
- `nbi_logs_filtered.log`
- `opticalcontroller_logs_since_2h.log`
- `opticalcontroller_logs_filtered.log`
- `serviceservice_logs_since_2h.log`
- `serviceservice_logs_filtered.log`
- `call_Dc79YLRvpLDAyCckth6hNgFy__vscode-1779785806596_content.txt`
- `call_BHBIbv0bW4CnxcHt9Tl8eXjI__vscode-1779785806600_content.txt`
- `call_tBlU3l6lwW9F0s3dnYoXGnEA__vscode-1779785806608_content.txt`
- `call_jbSHWprhlIisQtSvq1MdVM53__vscode-1779785806609_content.txt`

## What Is Happening

### 1) NBI fails service update with HTTP 500
From NBI logs around service update:
- `PUT /tfs-api/context/.../service/3c5e4a1c-22ca-48a6-9270-72eb21b0ad18 -> 500 INTERNAL SERVER ERROR`
- gRPC message: `"'str' object has no attribute 'keys'"`

### 2) Service component throws parsing exception
From service logs:
- `UpdateService exception`
- `AttributeError: 'str' object has no attribute 'keys'`
- Failure occurs at logic similar to: `if "optical_band_id" in reply_json.keys():`

This means the optical reply is a string (not a dict/JSON object), but service code assumes dict and crashes.

### 3) Optical controller returns failing HTTP codes
From opticalcontroller logs at same timestamp:
- `GET /OpticalTFS/GetTopology/admin/admin -> 400`
- `PUT /OpticalTFS/AddFlexLightpath/TP-DC1/TP-DC4/100/ANY/1/200 -> 404`

So optical path activation call chain fails before service can be marked `ACTIVE`.

## Why Service Stays PLANNED
Controller flow creates/reserves the service first and marks it `PLANNED`, then tries optical activation. Because optical activation fails and service code crashes during reply handling, final transition to `ACTIVE` never happens.

## Primary Root Cause
Mismatch/invalid opticalcontroller endpoint behavior for the call made by `serviceservice`:
- either endpoint/path/parameters are not valid for deployed opticalcontroller API,
- or topology/context inputs are invalid (`admin/admin`), causing non-JSON or error response,
- then service code does not defensively validate response type before accessing `.keys()`.

## Recommended Fix

### Immediate Operational Fix
1. Validate opticalcontroller API routes and parameters used by service component:
   - `GetTopology` route and required context/topology values.
   - `AddFlexLightpath` route and expected path arguments (`src`, `dst`, bitrate, preferred band, bidirectional flag, optical bandwidth).
2. Confirm that `TP-DC1` and `TP-DC4` names are valid identifiers expected by opticalcontroller route (name vs UUID mismatch can cause 404).
3. Fix topology/context mapping so service does not call `GetTopology/admin/admin` if that is invalid in this deployment.

### Code Hardening (Recommended)
Patch `tfs-ctrl` service logic (`ServiceServiceServicerImpl`) to:
1. Parse optical reply safely.
2. Check type before dict access (`isinstance(reply_json, dict)`).
3. If reply is not dict or includes error fields, return controlled provisioning error (no exception crash), keep useful error message.

Suggested guard pattern:
- if `reply_txt` is empty -> return clear optical error
- if JSON decode fails -> return clear optical error
- if parsed object is not dict -> return clear optical error with raw reply excerpt

### UX/Automation Recommendation (Agentic Module)
Treat optical service status `PLANNED` as asynchronous-in-progress unless explicit failure is returned, to avoid repeated duplicate create attempts while controller is still processing.

## Verification After Fix
Run one test intent:
- `create optical connectivity service between tp-dc1 and tp-dc4`

Then verify:
1. No 500 in NBI logs.
2. No `'str' object has no attribute 'keys'` in service logs.
3. No 400/404 in opticalcontroller for `GetTopology` and `AddFlexLightpath`.
4. Service transitions from `PLANNED` to `ACTIVE`.
