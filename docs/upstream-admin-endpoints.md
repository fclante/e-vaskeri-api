# Upstream admin, device and write endpoints

Reference for the parts of the Nortec "datpro2" backend (`https://back-nortec.datpro2.dk`) that the wrapper deliberately
**does not use**: machine/device control, landlord settings, and every function that changes state. It also documents the
unauthenticated `Location/Find5` exposure and contains a draft disclosure note.

> **Status: documentation only, not tested.** Nothing in this file was called with a live session. Everything comes
> from (a) the backend's public `.help` pages, requested with an **empty session** so no function could execute,
> (b) the SPA's JavaScript, and (c) `System/App3`, which lists the functions a tenant session can reach. Statements
> marked *inferred* are conclusions from the documented request/response shape. Verify them on a test tenant before relying on them.

Date of analysis: 2026-11-13. The backend is shared by ~20 white-label tenants (`back-<tenant>.datpro2.*`), so behaviour
may differ per tenant.

## 1. How calls work

```
GET https://back-nortec.datpro2.dk/<Class>/<Function><Version>/?App=TUK&session=<S>&tick=<ms>&native=false&<params>&.json
```

| Part | Notes |
|---|---|
| `App=TUK` | Mandatory. Without it: `Return 224 AppDenied`. |
| `session` | The only authentication. A header such as `Authorization: Bearer` is ignored (`208`). Query string only, so it ends up in logs and proxies. |
| `tick` | Client time in ms, echoed back as `Tick`. |
| `native` | `false` for the web client. |
| `&.json` | Format flag. It **must be a bare `.json`**. `.json=` (what most HTTP libraries send for an empty value) returns the HTML help page instead of data. Other formats: `.xml`, `.pdf`, `.help`. |
| Verb | Everything is a **GET**, including functions that write (see §3). `Units/Live1` is documented as "GET POST". |
| Status | Always HTTP 200. The outcome is the JSON `Return` field. |
| CORS | `Access-Control-Allow-Origin: *` and methods `HEAD, POST, GET, OPTIONS, PUT, DELETE, PATCH` on every endpoint I checked. |

### Session lifecycle

1. `System/Localization3?domain=e-vaskeri.dk` needs no auth and returns an **anonymous** `Session`.
2. `User/Login4?username=…&password=…&language=da&guid=&domain=…` returns `Users[0].Session`: the logged-in session.
   Credentials are sent in the query string of a GET.
3. Every response carries a new `Session` ("to be included in the next request"). In practice the **earlier session keeps
   working** (tested for read endpoints), so a leaked session stays valid. Real lifetime unknown.
4. Some responses embed *action sessions*: values that identify one specific action and are used in place of the login session:
   `Calendar5` turns (**"Session to use when booking"**, a reservation is one GET away), `Find5` locations (selects a
   location for sign-up), `AllInformations1.Tab.Session`, and settings `Form.Session`. Treat them like credentials.
   The wrapper never returns them.
5. `User/Verify1` validates a session. The SPA also accepts `?session=<S>` in its own URL.

### Return codes you will meet

| Code | Name | Meaning for these functions |
|---|---|---|
| 100 | OK | |
| 110 | OKGoHome | The tab is not enabled for this user/location, or the privacy policy is not accepted. |
| 114 / 222 / 223 | OKNoAccess / AccessDenied / IsReadOnly | What a tenant *should* get from an admin function. **Expected, not yet confirmed.** |
| 207 / 208 | SessionExpired / SessionError | Expired, missing or corrupt session. |
| 216 | NotFound | e.g. unknown `Document3` name. |
| 224 | AppDenied | Missing/wrong `App`. |

The full table of 83 codes is returned by `System/App3`.

## 2. Reaching the functions

`System/App3` returned **67 functions** for a normal tenant session, including every function in this file. Being listed
only means the function is routable. It does not prove the session is *authorised* for it. That check happens server-side,
and it has **not** been verified for tenant sessions.

## 3. Functions that write (all via GET)

| Function | Parameters | Effect |
|---|---|---|
| `User/Settings5` | `NotifyDone`, `NotifyChange`, `NotifyBook`, `Language` | **Empty params = read** (`User{Text,Number,SMS,Email,Language,NotifyDone,NotifyChange,NotifyBook}`, `Push`). Any non-empty value **writes**. `NotifyDone/Change`: -1 irrelevant, 0 no, 1 yes. `NotifyBook`: -1, 0, 30, 60, 90, 120, 240, 480 (minutes before). Languages: da en sv fi de fr es eu it so tr ar cy no kl. |
| `User/Notifications1` | `name`, `value` (per SPA) | Sets one notification preference. No `.help` text. |
| `User/CodeSave1` | `Code`, `Password`, `PasswordNew1`, `PasswordNew2` (+ SMS/e-mail per docs) | Change phone, e-mail or password. Needs a one-time `Code` (single use). Changing the password logs the user out. |
| `User/Code5` | `Reason` 1 forgot, 2 activate, 3 new user, 4 change username, 5 change password, 6 close account; `Username`, `Activation`, `Type` | **Sends** a one-time code by SMS or e-mail to the user. Reasons 1-3 are pre-login flows, so it is probably reachable without a login and would be an SMS/e-mail pump if not rate-limited *(inferred, not tested)*. |
| `User/CloseAccount1` | `Code` | Closes the account (two steps: the form returns a `Tab` with `Sure:false` and a session, then `Sure` confirms). |
| `User/Tag4`, `User/TagNew1`, `User/ScanTag1`, `User/QRTag1` | `TagNew1`: `Fullname`, `Address`, `Address2`, `Zip`, `Amount`, `Delivery`, `Url`; `ScanTag1`: `Barcode` | Lock/unlock key tags, order a new tag, attach a tag by barcode/QR. |
| `User/Transfer4`, `User/Pay1`, `User/PayFree1`, `User/Callback4`, `User/Receipt4` | `Amount`, `Url` (return URL) | Payment flow: builds an order and returns a POST form (`Pay.Posts[]`, `Pay.Url`) for the payment provider. `Receipt4 ?url=` reads an order receipt (probably read-only). `PayFree1` has no help page. |
| `User/Logout3` | none | Ends the session. |
| `User/StorePushNotification1/2`, `System/PNR1` | `Token`, `Tag`, `TabId`, `Title`, `Body` (+ `AppIdentifier`, `DeviceId`, `Crc`, `Version`) | Registers a push-notification device. |
| `User/Hidden1` | none | UI section, purpose unclear. Not called. |
| `User/Statement6` | none | Listed in `App3`; its `.help` URL returned a small PNG instead of a help page. Unknown. |

**Consequence:** because writes are GETs, any request to this backend can mutate state: prefetching, link scanners, retries
and cached "reads" are all dangerous. The wrapper only ever calls an explicit allow-list of read functions (§6).

## 4. Machine and device functions (`Units/*`, `Tag/*`, `System/*`)

None of these are used by the SPA.

| Function | Documented parameters | Documented response | Notes |
|---|---|---|---|
| `Units/Action1` | none documented | none | With an empty session the backend answers with an **unhandled .NET exception page** (§7), so it clearly executes server-side logic that reads `userIds` and a portal balance. Purpose unknown. |
| `Units/Cmd1` | `App`, `Session` | none | Name suggests a machine command. *Inferred; arguments are undocumented.* |
| `Units/Display1` | `App`, `Session` | none | Name suggests a machine display/status text. |
| `Units/Live1` | `App`, `Session` | none | "GET POST". Per-unit live data, probably what feeds the unit SVGs. |
| `Units/ChargePoint1` | `LS`, `SS`, `Go`, `WL`, `Action`, `Language` | none | Likely EV charge-point control (there is also `Location/ChargePoints1`, which returned `110` here). Abbreviations unexplained. |
| `Tag/Release3` | none documented | `TagId`, `Count`, `Post`, `Alive` | The help page's example response is `224`. Presumably releases/unlocks a tag. |
| `System/Setup3` | `Part`, `Version` | `Filename`, `Source`, `Data`, `Offset`, `Length`, `Version` | Chunked **file transfer** (offset/length). Its `.help` page alone is ~700 KB. Looks like device setup/firmware distribution. *Inferred.* |
| `System/Link1`, `System/FT3` (`Text`), `System/EasyPros1` | `App`, `Session` | none | Undocumented. |
| `System/HelloWorld1` | `App`, `Session` | none | "Test request". Safe liveness check candidate, not verified. |

## 5. Landlord / location settings (`Location/Settings*`)

These appear to be the administration screens of the SPA ("Administration" tab placement exists in the `Tab.Pos` enum).

| Function | Parameters | Response |
|---|---|---|
| `Location/Settings1` | `Name`, `Value` | Server-driven form: `Sections[].Rows[].Form {Title, Text, Session, Name, Inputs[]{Name, Text, Type:"text", Value, Status, Units, Help, Focus}}`. |
| `Location/SettingsPrice1` | `Name`, `Value` | Same form shape, for the price list. |
| `Location/SettingsOpeningHours1` | `Name`, `Value` | Same form shape, for opening hours. |
| `Location/SettingsHistory1` | `Name` | `Histories[]{Date, Value, User}`: audit trail of changes to one input. |
| `Location/SettingsHelp1` | `Name` | `Title`, `Lines[]{Point, Text}`: help text for one input. |

*Inferred protocol:* call without `Name`/`Value` to read the form. Each input has a `Name`. Submitting `Name=<input>&Value=<new>`
(with the form's own `Session`) writes the setting, and `SettingsHistory1?Name=` shows who changed it. This was **not**
exercised. The important open question for the owner: **can a tenant session read or write these?** A correct deployment
returns `222 AccessDenied` / `114 OKNoAccess` for tenants.

## 6. What this wrapper does about it

- Only these upstream functions are called: `User/Login4` (creates the session), `Location/Live5`, `User/Statement5`,
  `User/Statement4`, `Location/Calendar5`, `Location/Prices1`, `User/Messages1`, `User/Refresh1`, and the public unit SVGs.
  Apart from the login they are all reads. Anything else must be added deliberately.
- `/api/v1/calendar` drops the per-turn booking `Session`, and `/api/v1/messages` masks one-time codes and e-mail
  addresses (the message list contains copies of account e-mails).
- Query strings are never logged, and `.json` is always sent bare.
- No code path exposes `Location/Find5`, so the wrapper cannot be used to enumerate laundry rooms.

## 7. Findings for the operator

| # | Finding | Evidence |
|---|---|---|
| 1 | Admin/device functions are routable for tenant sessions. Authorisation is unverified. | `System/App3` lists `Units/*`, `Location/Settings*`, `System/Setup3`, `Tag/Release3`. |
| 2 | **Information disclosure:** `Units/Action1` (empty session) returns the standard ASP.NET "Server Error" page with a full stack trace: internal class names, source file paths on the server, and a developer's Windows account name in a build path. | One GET of `/Units/Action1/?App=TUK&session=&.help`. |
| 3 | State changes via GET + wildcard CORS + session in the query string. | §1, §3. |
| 4 | `Calendar5` hands out a per-turn "booking session": a capability URL for a reservation. | `Calendar5` help: "Session to use when booking". |
| 5 | `User/Code5` is documented with pre-login reasons (forgot password, activate, new user) and sends SMS/e-mail, so it is probably reachable unauthenticated. Rate limiting unverified. | `.help` text. *Not triggered, inferred.* |
| 6 | `Location/Find5` is unauthenticated (no session needed) and bulk-enumerable. | §8. |

How to verify 1 safely: with a **test tenant account** (never a real resident's), call each function in §4 and §5 once with
no parameters and record `Return`. Expected: `222`/`114`. Anything `100` means authorisation is missing.

## 8. `Location/Find5`: unauthenticated address enumeration

```
GET /Location/Find5/?App=TUK&session=&domain=e-vaskeri.dk&search=<text>&limit=<1-200>&.json
```

- `session` can be **empty**: no login and no anonymous session is needed (verified 2026-11-13). Any string, including an anonymous
  session from `System/Localization3`, works as well.
- `search` accepts name, zip code, activation code, GPS, BankID or location id. A single character is enough.
- Observed on 2026-11-13: `search=a&limit=10` returned 20 locations, `search=a&limit=200` returned **400**, and `search=1&limit=200`
  returned 130, all from single unauthenticated requests. The `limit` is applied per result group, not as a hard cap.
- Each result: `Texts[]` (opening date, street address, zip/city, `Lokation: <id>`), `Icons[]`, `Options{Email, Buttons:["CreateUser"]}`, a
  per-location `Session` (used to start sign-up at that location) and `GPS[]` (degrees × 1,000,000).
- Combined with `Access-Control-Allow-Origin: *`, any web page can run this from a visitor's browser.

**Impact:** a complete list of the tenant's laundry rooms with exact addresses and coordinates, plus a session per location
that the backend uses to select that location (for sign-up). Addresses of residential buildings are personal-data adjacent.
The `CreateUser` button suggests self-registration is possible, and activation codes are accepted as search terms *(inferred
from the help text, not tested)*.

**Suggested fixes (for the vendor):** require a logged-in or invitation-bound session for `Find5`; require a minimum search
length (≥3) and cap total results; rate-limit per IP; drop GPS and street-level text from anonymous results; stop accepting
activation codes in a free-text search.

### Draft disclosure note

> **DRAFT. Not sent. Review and fill in the contact details before use.**
>
> **To:** Nortec A/S, security / support contact for the e-vaskeri service, and the building administration
> **Subject:** Unauthenticated enumeration of laundry-room addresses (`Location/Find5`) and related API hardening
>
> Hello,
>
> While reviewing how the e-vaskeri web app talks to its backend I noticed behaviour that I would like to report in good faith.
> I only used read requests, did not log in as anyone other than myself, and did not try to access other users' data.
>
> 1. **`Location/Find5` needs no login and no session at all.** One request with an empty `session` and
>    `search=a&limit=200` returned 400 locations including street address, postcode, internal location id and GPS
>    coordinates, together with a per-location session. Reproduction: a single GET, see the parameters above.
> 2. **Machine, settings and setup functions are listed for ordinary tenant sessions** (`Units/Cmd1`, `Units/Action1`,
>    `Location/Settings*`, `System/Setup3`, `Tag/Release3`). I did not call them. Please confirm that each enforces
>    authorisation server-side.
> 3. **`Units/Action1` returns an unhandled-exception page** with a stack trace, internal source paths and a Windows user name.
> 4. **State-changing operations are GET requests**, sessions travel in the query string, and every endpoint answers with
>    `Access-Control-Allow-Origin: *`.
> 5. **`User/Code5` appears reachable without a session** (its reasons include "forgot password", "activate" and "new user",
>    which are pre-login flows) and sends SMS/e-mail. I did not call it. Please check rate limiting.
>
> Suggested mitigations: see the list above. I'm happy to retest once fixed and will not publish details before you have had
> reasonable time to respond.
>
> Kind regards,
> *<name / contact>*
