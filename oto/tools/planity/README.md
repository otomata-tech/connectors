# `oto.tools.planity` — a Planity salon's calendar and till

**Asynchronous**, **read-only** client for a Planity pro account. No write is exposed:
no creation, modification or cancellation of appointments.

The client connects with the account's email and password. There is no API key to
obtain, and there never will be.

## Install

```bash
pip install 'oto-core[planity]'
```

The extra brings `httpx` and `websockets`. Without it, importing the package raises an
error that says so — it is the only asynchronous client in the lib, and the two
dependencies serve only it.

## Use it

```python
from oto.tools.planity import PlanityClient, PlanityEndpoints, resolve_range

endpoints = PlanityEndpoints(
    firebase_api_key=...,   # the three endpoints of the Planity application,
    firebase_app_id=...,    # supplied by whoever deploys the connector
    rest_api=...,           # (root, no trailing slash)
)

async with PlanityClient("me@example.com", "password", endpoints) as planity:
    salons = await planity.list_salons()            # the `id` is used everywhere else
    gte, lte = resolve_range(preset="last_month")   # or date_from/date_to in ISO
    revenue = await planity.get_key_indicators(salons[0].id, gte, lte)
```

### What the client authenticates with

The Planity account's email and password, and nothing else: the client takes no
other authentication path of the Planity application. **What it can read is
therefore exactly what this account can read** — the scope is set by choosing the
account, not by configuring the client.

### The endpoints, and why they are not here

`PlanityEndpoints` has **no default value**, and never will. The three values are
public by design, they identify the Planity application and authorize nothing on
their own — what authorizes is the person's password.

Taking them out of here is therefore not a gesture of secrecy, it is one of
**genericity**: this repo is public and open source, a client published here
describes a protocol and does not hard-code a third-party company's constants, as
if it were its official integration. Whoever deploys the connector sets them, and
answers for what they call. A default would have put the constant back here under
another name.

### The rest, as a list

- **The endpoints are mandatory** — there is no "zero-configuration" mode.
- **Everything is asynchronous.** It is not a style choice: the upstream imposes it,
  and there is no synchronous equivalent to write.
- **Amounts are in cents** and **timestamps in milliseconds** — the client returns
  them as Planity gives them; `ms_to_iso` converts, and conversion to euros is the
  caller's job.
- **Date windows** go through `resolve_range(date_from, date_to, preset)`:
  presets `today`, `yesterday`, `this_week`, `last_week`, `this_month`, `last_month`,
  `ytd`, `7d`/`30d`/`90d`… Default: the last seven days, Europe/Paris timezone.
- **An account that authenticates without opening any salon** is an account with no
  establishment attached — not a failure, and nothing to retry.

### Reading BOUNDED

A Planity node carries a salon's entire history: thousands of appointments,
hundreds of till sessions, thousands of stock movements. An unbounded read is not a
slow read, it is a read that does not finish.

```python
from oto.tools.planity.firebase_ws import limit_last, range_on

await db.get("a/node", limit_last(20))                   # the last 20
await db.get("a/node", range_on("createdAt", a, b))      # a window
```

⚠️ **A bounded query requires a tag in the frame.** It is emitted by `get()`, and
nowhere else: without it, the upstream refuses with `permission_denied` — a refusal
that reads as a missing right when it is a protocol field that is missing.

### Appointments

```python
appointments = await planity.list_appointments(salon_id, "2026-09-01", "2026-09-07")
one = await planity.get_appointment(salon_id, vevent_id)          # or + employee_id
recurring = await planity.list_recurring_appointments(salon_id)
```

Three things decide it, and each fails by returning "nothing" rather than an error:

- the window is in **days** (`YYYY-MM-DD`), not timestamps: the upstream stores the
  salon's WALL-CLOCK time, with no offset, and converting to milliseconds loses or
  gains an hour depending on the season;
- an appointment is filed under the **calendar child** (the staff member), not under
  the calendar — `list_appointments` therefore sweeps all the salon's children, or
  the one you name;
- a **cancelled** appointment has no status, it has a deletion date:
  `cancelled=True`. It is returned like the others — filtering it out by default
  would hide cancellations from whoever looks for them.

**Recurring** appointments live in another node and appear in NO per-day read:
a calendar that only has recurrences reads as an empty calendar.

⚠️ **An appointment and a receipt carry the customer's contact details** (name,
phone, email; a receipt adds the address). The client returns what the upstream gives — it is
a library. What is exposed of it is decided above, and there is reduced to the
identifier.

### A service's price, and a product's stock

A service has no simple price: `services.prix()` returns a `kind` — firm,
range, on quotation, or **absent**. A `0` in its place would say "free", and a
zero never raises.

A product does not have a simple stock either: `stock.produit()` returns the list of
**purchase lots**, each with its purchase price. Its thresholds (`stock_threshold`,
`stock_ceiling`) are `None` when the salon does not use them — `None` is not
`0`, and confusing the two makes you order everything, all the time.


This file says how to use the package. The rest — how each transport
is spoken, and why — is read in the modules' code, next to what it
explains.
