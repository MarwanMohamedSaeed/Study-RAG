# Barbershop Management System (demo build)

A Django implementation of the original Bandaq Barbershop business proposal
([docs/Bandaq_Business_Proposal.pdf](docs/Bandaq_Business_Proposal.pdf)): one
thin, working path through every module in Section 7, so the demo maps 1:1 to
Sections 5–9. The running software itself is **white-label** — it isn't tied
to that or any other shop's name.

The shop name and the two money rules (loyalty discount, payroll split) are
owner-editable on the **Settings** page, not hardcoded — see below. A fresh
install starts on a placeholder name ("My Barbershop") until its owner types
their own.

**Full project documentation is in [PROJECT.md](PROJECT.md)** — architecture,
data model, business rules, workflows, testing and the demo script. This file is
the quick start.

## Stack

- Django 5.1 (MVT), SQLite, Django templates + Bootstrap 5, `django.test.TestCase`.

## Look and feel

The interface is a charcoal-and-brass theme ("Barbershop Luxe") built on
Bootstrap 5: Fraunces for headings and the wordmark, Inter for everything else,
warm paper background, white panels, brass reserved for the primary action and
active navigation.

**Everything is served locally** — Bootstrap, both fonts, and every icon. There
are no CDN links anywhere, so the demo looks identical with the wifi switched
off. Icons are an inline SVG sprite in [`templates/_icons.html`](templates/_icons.html),
not emoji, so they inherit colour and stroke weight from the theme.

Design decisions worth mentioning if asked:

- **Colour is never the only signal.** A low-stock row is red *and* carries a
  "Low stock" pill with a warning icon; a loyalty discount is green *and* says
  "10% off next visit".
- **Contrast.** Measured, not assumed: body text 15.6:1 on the page background,
  muted text 5.7:1, brass under white button text 5.1:1 — all above the WCAG AA
  threshold of 4.5:1.
- **Numbers use tabular figures**, so money columns don't shuffle sideways as
  digits change, and are formatted with thousands separators.
- **Motion respects `prefers-reduced-motion`** and every interactive element has
  a visible focus ring.
- Tables scroll inside their own container, so no page scrolls sideways on a
  phone (checked at 390px).

All the theme's tokens live at the top of [`static/app.css`](static/app.css).

## Run it

```bash
.venv/Scripts/python.exe manage.py runserver
```

First-time setup on a fresh machine:

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt && .venv/Scripts/python.exe manage.py migrate && .venv/Scripts/python.exe manage.py seed_demo
```

Then open http://localhost:8000/.

Reset the demo data at any time (this also puts customer `01099999999` back on
5 visits, ready for the loyalty demo):

```bash
.venv/Scripts/python.exe manage.py seed_demo --reset
```

## Run the tests

```bash
.venv/Scripts/python.exe manage.py test
```

60 tests: the six acceptance cases from Section 12.2 (TC01-TC06), five further
cases (TC07-TC11) written to probe the edges, and tests for everything added
since (employee phone/national ID, customer state, phone-number customer
search/create, owner-configurable payroll split and loyalty rule, Shop
Settings). See [docs/Barbershop_Test_Case_Report.pdf](docs/Barbershop_Test_Case_Report.pdf)
for TC07-TC11 and the two defects they found, and
[docs/Barbershop_Full_Test_Report.pdf](docs/Barbershop_Full_Test_Report.pdf) for the
full regression pass across every feature (2026-08-27, no defects found).

## Admin access

`/admin/` needs a login (nothing else in the app does):

```bash
.venv/Scripts/python.exe manage.py createsuperuser
```

## Modules (Section 7)

| App | Module |
| --- | --- |
| `employees` | Employee Registry |
| `customers` | Customer Registry + loyalty logic |
| `catalog` | Haircut Catalog, Add-ons, Packages |
| `transactions` | Transactions & Ratings |
| `payroll` | Payroll Engine |
| `inventory` | Inventory Tracking |

Built in that order (V1 → V2 → V3 dependency chain): each app is runnable and
demoable on its own before the next one starts.

## The two business rules, with their numbers

Both are **owner-configurable** from the Settings page (`/settings/`) — the
numbers below are the defaults a fresh install starts on, not fixed rules.

**Loyalty discount (FR3 / TC01).** The proposal says "the defined threshold"
without a number. This build defaults it to:

> Every **5 completed visits** earn the customer **10% off** their next visit.

So a discount applies when `visit_count` is a non-zero multiple of the
threshold (5 by default) — the 6th, 11th, 16th haircut and so on. It is applied
automatically when a transaction is logged, before the new visit is counted.
The owner edits both numbers on `/settings/`; stored in
[`bandaq/models.py`](bandaq/models.py) as `ShopSettings.loyalty_visit_threshold`
and `loyalty_discount_percent`, read live by
[`customers/models.py`](customers/models.py)'s loyalty methods.

**Payroll split (Section 8).** Barbers keep 70% of the revenue they generate,
the shop keeps 30%, by default — the owner can type a different barber
percentage directly into the **Run Monthly Payroll** form each time; leaving it
blank keeps 70/30. Defaults defined in
[`payroll/models.py`](payroll/models.py) as `BARBER_SHARE` / `SHOP_SHARE`; the
percentage actually used is stored per month on
`PayrollRecord.barber_share_percent`. The shop takes the rounding remainder, so
the two halves always add back up to the total.

## Demo script (about 5 minutes)

1. **Dashboard** (`/`) — counts per module, this month's revenue, the low-stock
   banner. Every number comes from a different app.
2. **Employees** (`/employees/`) — add a barber, edit them, then try to delete a
   barber who has transactions: the delete is blocked, so no transaction is
   orphaned (TC06).
3. **Customers** (`/customers/`) — `01099999999` shows "10% off next visit" at 5
   visits. Click through to their visit history.
4. **Catalog** (`/catalog/`) — haircuts, add-ons, and packages. "Full Groom"
   costs 250 EGP fixed while its parts add up to 260 EGP (TC05).
5. **Log a transaction** (`/transactions/log/`) — pick Kareem, then *type*
   `01099999999` into the customer phone field (no dropdown — a new number
   creates a new customer automatically) + Skin Fade + Beard Trim, rate all
   three. The 10% loyalty discount is applied automatically: 220 EGP base →
   198 EGP paid (TC01, TC03).
6. **Payroll** (`/payroll/`) — press **Run Monthly Payroll**, optionally typing
   a barber percentage first. Each barber gets one record: customers served,
   revenue generated, salary, shop share (TC02). Re-running the month
   recalculates rather than duplicating.
7. **Inventory** (`/inventory/`) — Hair Gel and Razor Blades sit below their
   thresholds and their rows are red, with an alert banner on top. Press `−1` on
   any item to push it under its threshold live (TC04).
8. **Settings** (`/settings/`) — change the shop name or the loyalty numbers;
   every other page picks it up immediately.

## Test cases (Section 12.2)

| Case | What it checks | Where |
| --- | --- | --- |
| TC01 | Loyalty discount applied at the threshold | `customers/tests.py`, `transactions/tests.py` |
| TC02 | Payroll 70/30 split (35,000 → 24,500 / 10,500) | `payroll/tests.py` |
| TC03 | Transaction stores barber, customer, service, 3 ratings | `transactions/tests.py` |
| TC04 | Low-stock alert when quantity drops to the threshold | `inventory/tests.py` |
| TC05 | Package charges its fixed price, not the sum of parts | `catalog/tests.py` |
| TC06 | Employee CRUD leaves no orphaned transactions | `employees/tests.py` |

## Additional test cases (TC07–TC11)

Written after TC01–TC06 were green, to test the edges rather than the happy path.
Full write-up in [docs/Barbershop_Test_Case_Report.pdf](docs/Barbershop_Test_Case_Report.pdf).

| Case | Kind | What it checks | Outcome |
| --- | --- | --- | --- |
| TC07 | Negative | Duplicate customer phone number is rejected | Pass |
| TC08 | Boundary | Ratings must be 1–5, on every write path | **Found D-01** |
| TC09 | Regression | Payroll counts only the selected month (Dec → Jan rollover) | Pass |
| TC10 | Integration | Package fixed price survives form → pricing → discount → DB | **Found D-02** |
| TC11 | Security | No GET changes data; POSTs need a CSRF token | Pass |

Two defects found and fixed:

- **D-01** — the 1–5 rating rule was enforced only by the form. Model validators
  don't run on `save()`, so the admin or shell could store a 0 or a 6 and skew the
  barber averages. Fixed with three `CheckConstraint`s on `Transaction.Meta`
  (migration `transactions/0002`).
- **D-02** — ticking add-ons alongside a package attached them to the transaction
  without charging for them, invisibly. Fixed in `TransactionForm.clean()`, which
  now rejects that combination with an explanatory message.

## Documents

| File | What it is |
| --- | --- |
| [docs/Bandaq_Business_Proposal.pdf](docs/Bandaq_Business_Proposal.pdf) | Business proposal: problem, product, pricing, ROI, go-to-market |
| [docs/Barbershop_Test_Case_Report.pdf](docs/Barbershop_Test_Case_Report.pdf) | TC07-TC11 with rationale and the two defects found |
| [docs/Barbershop_Full_Test_Report.pdf](docs/Barbershop_Full_Test_Report.pdf) | Full regression + feature verification, 2026-08-27 |
| [PROJECT.md](PROJECT.md) | Full technical reference |

## Where the code deviates from the Section 9.2 class diagram

`Transaction` also stores **what was sold** (`package`, `haircut`, `addons`) and
the pricing breakdown (`base_price`, `discount_amount`) alongside `final_price`.
The class diagram lists only `final_price`, but the demo flow in Section 5.5
("pick barber + customer + service → auto-applies loyalty discount") needs the
service link to price the haircut and the discount figure to show the customer
what they saved. `ShopSettings` (shop name, loyalty rule) and
`PayrollRecord.barber_share_percent` aren't in the diagram either — see
[PROJECT.md §7](PROJECT.md#7-where-the-code-deviates-from-the-class-diagram)
for why. Everything else follows the diagram as written.
