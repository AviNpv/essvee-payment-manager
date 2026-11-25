# ESSVEE Associates – WhatsApp Payment Collection System

A private, admin-controlled payment collection application. Customers are tracked by **outstanding balance** (never by invoices); approved WhatsApp templates carry a **Pay Now** button. By default the button opens an HTTPS redirect that launches the customer's UPI app chooser (GPay, PhonePe, Paytm, etc.), where the customer can enter the amount they want to pay. Razorpay Payment Links remain available as an optional provider. No card/UPI secrets are collected or stored.

**Live app:** https://essvee-payment-manager.onrender.com · **Source:** https://github.com/AviNpv/essvee-payment-manager

## 1. What this system does

- Maintains customers, phone numbers, consent and outstanding balances.
- Imports Excel/CSV customer balances with preview, validation and audit trail.
- Creates and reuses payment links for the configured provider:
  - **UPI (default):** HTTPS redirect → `upi://pay` → phone shows installed UPI apps; amount may be entered by the customer.
  - **Razorpay (optional):** official hosted Razorpay Payment Link for the outstanding balance.
- Sends Meta WhatsApp Cloud API **approved templates only**, with a URL button, consent enforcement, opt-out handling, daily limits, quiet hours, idempotency and message history.
- Records Razorpay webhooks and manual/UPI collections as **AWAITING_CONFIRMATION**. Balances change only after an administrator explicitly confirms; rejections never touch balances. UPI deep links do not verify payment by themselves — the UTR is confirmed by your team.
- Provides a balance ledger (`OPENING`, `CHARGE`, `PAYMENT`, `ADJUSTMENT`, `REVERSAL`), dashboard, collections/outstanding/message reports and exports.
- Runs scheduled reminder, failed-message retry and payment-link reconciliation jobs (all disabled until you enable automation).

### Explicit non-goals

- **No invoices:** no invoice numbers, creation, due dates, management or invoice-driven reminders.
- **No fake provider success:** missing credentials produce configuration errors/screens.
- **No browser automation/scraping:** WhatsApp is sent only via the official Graph API.
- **No auto-approval:** browser redirects and webhooks never confirm a payment.

## 2. Architecture and project structure

```text
ESSVEE PAYMENT COLLECTION/
├── Dockerfile                  # Multi-stage frontend build + FastAPI runtime
├── docker-compose.yml          # App + PostgreSQL 16
├── render.yaml                 # Render Blueprint (web service + managed DB)
├── .env.example                # All supported environment variables
├── backend/
│   ├── alembic.ini
│   ├── migrations/             # Initial schema revision 638b1677c532
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── tests/                  # 67 automated tests
│   ├── smoke_test.py           # Manual backend end-to-end smoke script
│   └── app/
│       ├── api/                # auth, customers, excel, pay, payments, whatsapp,
│       │                       # webhooks, reports, settings, system routers
│       ├── core/               # config, security, errors, rate limiting
│       ├── db/                 # engine/session and bootstrap seed
│       ├── models/entities.py  # All ORM tables
│       ├── schemas/__init__.py # Pydantic request/response models
│       ├── services/           # balance, customer, payment, upi, razorpay, whatsapp,
│       │                       # reminder, excel, reports, settings, audit
│       ├── templates/whatsapp_templates.json
│       └── main.py             # FastAPI app, scheduler, CORS, SPA fallback
└── frontend/
    ├── src/
    │   ├── pages/              # Login, Dashboard, Customers, Payments, Reminders,
    │   │                       # Messages, Imports, Reports, Settings, System
    │   ├── components/         # Layout/sidebar/theme, UI primitives
    │   ├── lib/                # Axios client, formatting, toasts
    │   └── App.jsx             # Routes and auth guard
    └── dist/                   # Built React assets served by FastAPI in production
```

Stack: React 18 + Vite + Tailwind CSS + Recharts + Lucide; FastAPI + SQLAlchemy + Pydantic + Alembic + PostgreSQL + Pandas/OpenPyXL + requests + APScheduler. Timezone: Asia/Kolkata.

## 3. Quick launch on Windows

### 3.1 Local development (SQLite, two processes)

These commands assume the repository is `C:\Users\<you>\Documents\ESSVEE PAYMENT COLLECTION` and that Python 3.11+ and Node 18+ are installed.

```powershell
cd "C:\Users\<you>\Documents\ESSVEE PAYMENT COLLECTION"
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt

$env:DATABASE_URL = 'sqlite:///./dev_test.db'
$env:SECRET_KEY = 'a-long-random-secret-for-local-development'
$env:ENABLE_SCHEDULER = 'false'
cd backend
& "..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open a second PowerShell window for the frontend:

```powershell
cd "C:\Users\<you>\Documents\ESSVEE PAYMENT COLLECTION\frontend"
npm install
npm run dev
```

- App: <http://localhost:5173>
- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>
- Seed login: `admin@essvee.in` / `ChangeMe123!` — change it immediately via **Settings → Security / Change password**.
- Before sending Pay Now messages, open **Settings → UPI** and enter your real `UPI ID / VPA` and `Public base URL`. For local HTTP testing use `http://localhost:8000`; production must use `https://…` because Meta requires HTTPS URL buttons. Keep **Customer enters amount** unless you want the UPI app pre-filled with the outstanding amount.

The corrected backend command from the repository root is:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Run it with `backend` as the working directory so `app.main` resolves.

### 3.2 Docker Compose (production-style, single service)

1. Copy `.env.example` to `.env` and replace `SECRET_KEY`, `POSTGRES_PASSWORD`, bootstrap admin password and provider credentials.
2. Run `docker compose up -d --build`.
3. Apply migrations: `docker compose exec app alembic upgrade head` (the container also runs this automatically at startup).
4. Open <http://localhost:8000>. The same container serves the built React app and the API.
5. Set `PUBLIC_BASE_URL=https://<your-domain>` in `.env` before sending UPI Pay Now messages.
6. Webhooks (Razorpay webhook only needed if you choose Razorpay as the provider):
   - Razorpay: `https://<your-domain>/api/webhooks/razorpay`
   - Meta: `https://<your-domain>/api/webhooks/whatsapp`

Docker is not installed in the build environment used to create this repository, so Compose configuration was written but could not be executed locally.

## 4. Database schema

All IDs are UUIDs. Every money column is `Numeric(16, 2)` mapped to Python `Decimal`; floats are never used for financial values. Datetimes are normalized to timezone-aware UTC.

| Table | Purpose / key columns |
| --- | --- |
| `users` | Login, bcrypt password hash, role, active flag. |
| `customers` | Name, unique E.164 WhatsApp number, consent, status, opening/outstanding/paid totals, reminder exclusions/counters. |
| `balance_transactions` | Append-only ledger: customer, amount, type, effect, description, balance after, actor, payment link. |
| `payments` | Amount, method, status, gateway IDs, link, reference, verification/rejection metadata. Unique gateway payment ID. |
| `payment_links` | Provider reference (`upi:<token>` or Razorpay ID), Pay Now URL, amount, status, expiry, attempts, provider metadata. |
| `whatsapp_messages` | Direction/type/template/status, provider ID, idempotency key, preview, button URL, payload, error/retry count. |
| `webhook_events` | Provider, event ID, raw payload, signature validity, processing status/error. |
| `reminder_settings` | Automation pause, frequency/interval, maximum reminders, daily limit, quiet hours, templates, retry/link/payment policy. |
| `app_settings` | Encrypted configuration values (Fernet derived from `SECRET_KEY`). |
| `import_history`, `import_rows` | Excel batch metadata, per-row validation and balance differences. |
| `audit_logs` | Actor, action, entity, before/after JSON, IP and user agent. |
| `scheduler_runs` | Job name, start/finish, status, processed/failed counts, details. |

Balance invariants:

- Customer balance changes and ledger inserts are committed in the same transaction.
- Overpayments that would produce a negative balance are rejected unless explicitly allowed with `allow_negative` on an adjustment.
- Confirming a payment restores/recomputes the ledger state; reversing writes an `INCREASE` ledger row.
- A payment confirmation message failure is reported as `message_status`; it never rolls back the confirmed financial state.

## 5. API endpoints

All endpoints except `/health`, `/api/health` and webhooks require `Authorization: Bearer <token>` after `POST /api/auth/login`.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/auth/login` | Obtain JWT. |
| GET | `/api/auth/me` | Current user. |
| POST | `/api/auth/change-password` | Change password. |
| GET/POST | `/api/customers` | List/search/filter or create customer. |
| GET/PUT/DELETE | `/api/customers/{id}` | Read, update or delete. |
| POST | `/api/customers/{id}/archive` | Archive customer. |
| POST | `/api/customers/{id}/toggle-exclusion` | Exclude/include from reminders. |
| GET | `/api/customers/{id}/history` | Ledger/payment/message history. |
| POST | `/api/customers/{id}/adjustments` | CHARGE or ADJUSTMENT ledger entry. |
| POST | `/api/excel/preview` | Upload XLSX/CSV and validate rows. |
| POST | `/api/excel/import` | Confirm preview with explicit confirmation. |
| GET | `/api/excel/template` | Download import template. |
| GET | `/api/excel/export` | Export customers XLSX/CSV. |
| GET | `/api/excel/imports` | Import history. |
| GET | `/api/payments` | List/filter payments. |
| POST | `/api/payments/manual` | Create payment awaiting confirmation. |
| POST | `/api/payments/{id}/confirm` | **Explicit admin approval**; changes balance. |
| POST | `/api/payments/{id}/reject` | Reject without balance change. |
| POST | `/api/payments/{id}/reverse` | Reverse confirmed payment with ledger entry. |
| GET | `/api/payment-links` | Recent links. |
| POST | `/api/payment-links/generate` | Create/reuse/force-new link for the active provider. |
| POST | `/api/payment-links/{id}/cancel` | Cancel link. |
| POST | `/api/whatsapp/reminders/send` | Send one reminder using an approved template. |
| POST | `/api/whatsapp/reminders/preview` | Bulk preview with counts and 10-minute confirmation token. |
| POST | `/api/whatsapp/reminders/bulk` | Bulk send; token mandatory. |
| GET | `/api/whatsapp/messages` | Message history. |
| POST | `/api/whatsapp/messages/{id}/retry` | Retry failed message. |
| GET | `/api/whatsapp/templates` | Template definitions to submit to Meta. |
| POST | `/api/whatsapp/test` | Connection test. |
| GET/POST | `/api/webhooks/whatsapp` | Meta verification, statuses, STOP/opt-out. |
| POST | `/api/webhooks/razorpay` | HMAC-verified payment events → pending payments (Razorpay mode). |
| GET | `/pay/{token}` | Public UPI redirect used by the WhatsApp Pay Now button. |
| GET | `/api/reports/dashboard` | Aggregate dashboard. |
| GET | `/api/reports/{name}` | collections/weekly/monthly/outstanding/whatsapp/etc. |
| POST | `/api/reports/export` | XLSX/CSV report export. |
| GET/PUT | `/api/settings`, `/api/settings/{group}` | Read/update configuration; secrets masked. |
| PUT | `/api/settings/reminder`, `/api/settings/payment` | Automation and payment policy. |
| POST | `/api/settings/clear/{key}` | Clear a credential. |
| POST | `/api/settings/test/{provider}` | Provider connection test (no fake success). |
| GET | `/api/audit-logs`, `/api/webhook-events`, `/api/scheduler/runs` | Audit/debug views. |
| POST | `/api/scheduler/run?job=reminders|retry|reconcile` | Manual job trigger. |
| GET | `/health`, `/api/health` | Liveness and DB status. |

Interactive OpenAPI documentation is at `/docs`.

## 6. Environment variables

Copy `.env.example`. The application reads these both from the environment and a `.env` file at the backend working directory (or repository root for Compose).

- **Application:** `APP_NAME`, `COMPANY_NAME`, `ENVIRONMENT`, `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `CORS_ORIGINS`, `APP_PORT`.
- **Database:** `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL` (PostgreSQL in production, SQLite accepted for local tests), `DB_ECHO`.
- **Bootstrap admin:** `ADMIN_EMAIL`, `ADMIN_PASSWORD` (only used when the users table is empty).
- **Payment provider:** `PAYMENT_PROVIDER=UPI|RAZORPAY`.
- **UPI:** `UPI_VPA`, `UPI_PAYEE_NAME`, `PUBLIC_BASE_URL`, `UPI_AMOUNT_MODE=CUSTOMER_ENTERS|FIXED_OUTSTANDING`.
- **Razorpay (optional):** `RAZORPAY_MODE` (`test`/`live`), `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_CALLBACK_URL`.
- **WhatsApp:** `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WABA_ID`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_API_VERSION`.
- **Automation:** `ENABLE_SCHEDULER`, `TIMEZONE`.

Secrets entered in the Settings UI are stored encrypted in `app_settings`; the API only returns masked values. A masked value submitted on save means “leave unchanged”.

## 7. Meta WhatsApp setup

1. Create/use a Meta Business App with WhatsApp Cloud API.
2. Obtain a permanent/system-user access token with WhatsApp messaging permissions, plus Phone Number ID, WABA ID and App Secret.
3. Set the webhook callback URL to `https://<domain>/api/webhooks/whatsapp` and the verify token to the value configured in `WHATSAPP_WEBHOOK_VERIFY_TOKEN`. Subscribe to `messages`.
4. Submit the three templates in `backend/app/templates/whatsapp_templates.json` for approval (`UTILITY`, English): `payment_reminder`, `payment_confirmation`, `partial_payment_reminder`. `payment_reminder` and `partial_payment_reminder` require a URL button labelled **Pay Now** with a variable URL token `{{1}}`; the backend supplies an `https://…/pay/<token>` redirect at send time. Keep the URL variable as HTTPS — `upi://` is not accepted by Meta template URL buttons.
5. Wait for Meta approval. Until templates exist/credentials are configured, sends are recorded as `FAILED` with an actionable error — never as fake successes.
6. Consent defaults to `OPT_IN` for imported/created customers with a valid number; `STOP` inbound messages set `OPT_OUT` and permanently exclude the customer.

## 8. Payment provider setup

### UPI app chooser (default)

1. Open **Settings → UPI**. Set **Payment provider** to `UPI app chooser`.
2. Enter a real collecting **UPI ID / VPA** (for example `yourname@bank`) and the **Payee name** shown inside UPI apps.
3. Enter the **Public base URL** — the public HTTPS address of this application (for local testing `http://localhost:8000` is allowed). Meta requires the WhatsApp button URL to be HTTPS.
4. Keep **Customer enters amount** so the UPI app opens without a fixed amount and the customer pays what they want. Choose **Pre-fill outstanding amount** only if you want the app to suggest the exact balance.
5. Use **Check setup** to validate the configuration without sending anything.
6. Flow: WhatsApp **Pay Now** → `https://<domain>/pay/<token>` → automatic `upi://pay?pa=…&pn=…` redirect → phone displays GPay, PhonePe, Paytm and other installed UPI apps.
7. UPI deep links do **not** notify this server about payments. The customer shares the UTR, and your team records it in **Payments → Record payment** with method `UPI` and the UTR as reference. Balances change only after an administrator clicks **CONFIRM**.

### Razorpay (optional)

1. Use TEST mode first. Create an API key pair and copy Key ID/Secret into Settings or environment.
2. Add webhook `https://<domain>/api/webhooks/razorpay`, choose a webhook secret, and subscribe to `payment.captured`, `payment.failed`, `payment_link.paid` (plus any additional payment events you want recorded).
3. Set `RAZORPAY_MODE=live` only after live credentials and live webhook secret are configured.
4. Payment Links are created through `POST /payment_links` with `notes.customer_id`, amount in paise, currency INR and optional callback URL/partial payment settings.
5. Webhook signatures are verified with HMAC-SHA256 before processing. Invalid attempts are stored for audit and return 401. Captured/failed webhooks create pending/failed payment rows; they **never** update balances.
6. Do not trust the browser return URL: the admin confirms payments explicitly in **Payments**.
7. To switch back to Razorpay at any time, set **Settings → UPI → Payment provider** to `Razorpay Payment Link`; existing active links are cancelled when a new link is generated.

## 9. Reminder rules

Eligibility requires: non-zero outstanding balance, `ACTIVE` status, opt-in consent, not excluded, no awaiting-confirmation payment, no reminder within the configured interval, under `max_reminders`, outside quiet hours, and within the global daily limit. Manual and bulk sends reuse a payment link per customer to preserve idempotency. Bulk send requires a preview token containing the eligible IDs (10-minute expiry); targeting an ID not in the token is rejected. Automation is off by default and can be paused at any time.

## 10. Deployment

### Live deployment (current)

| Item | Value |
| --- | --- |
| Application | https://essvee-payment-manager.onrender.com |
| Health check | https://essvee-payment-manager.onrender.com/health |
| API health | https://essvee-payment-manager.onrender.com/api/health |
| GitHub repository | https://github.com/AviNpv/essvee-payment-manager |
| Render Blueprint | `essvee-payment-manager` (ID `exs-dc35m10m7kps73d8o87g`) |
| Render web service | `essvee-payment-manager` (ID `srv-dc35mdrrjlhs73es4e00`) |
| Render database | `essvee-db` (ID `dpg-dc35m4jrjlhs73es3gsg-a`) |
| Region | Oregon (web service and PostgreSQL are in the same region so the private database hostname resolves) |
| Plan | Render free tier (web service sleeps when idle; free PostgreSQL expires 30 days after creation) |
| UptimeRobot monitor | `ESSVEE Payment Manager - health` (ID `804801244`) — HTTP(S), 5-minute interval, checks `/health` |

The initial Blueprint deployment failed because the web service was created in Singapore while the database was in Oregon. Those resources were removed and recreated with both in Oregon; the live service is verified `Live` and `/health`, `/api/health`, `/docs` and `/` all return HTTP 200.

**Before sending payment reminders:** sign in and open **Settings → UPI** to enter the real UPI ID / VPA. It was intentionally left unset during deployment because it is business-specific; the app shows a configuration screen rather than generating invalid links. Change the generated admin password immediately after first login.

### Render Blueprint

`render.yaml` provisions a managed PostgreSQL database and a Docker web service. Set `ADMIN_PASSWORD`, `UPI_VPA`, `UPI_PAYEE_NAME`, `PUBLIC_BASE_URL` (your real `https://<service>.onrender.com` address) and all provider secrets in the Render dashboard (marked `sync: false`). After deployment, run migrations once via Render Shell:

```bash
cd backend
alembic upgrade head
```

The container start command also runs `alembic upgrade head`. Render supplies a `postgres://` URL; the application normalizes it to `postgresql+psycopg://` automatically.

### Docker

The root `Dockerfile` builds the Vite app, copies `frontend/dist` into the Python image, installs backend requirements and starts Uvicorn on port 8000 with Alembic migrations. Compose wires PostgreSQL with a health check and persistent volume.

## 11. Phase completion record

| Phase | Delivered | Verification |
| --- | --- | --- |
| 1 – Foundation | Config, security, errors, rate limiting, DB session, ORM models, Alembic. | Initial migration applied and Alembic check clean. |
| 2 – Schemas/services | Pydantic schemas plus audit, settings, balance, customer, payment, provider, reminder, Excel and report services. | Service unit coverage and smoke checks. |
| 3 – Customer/import APIs | Customer CRUD/history/adjustments, Excel preview/import/export. | Customer/Excel tests, template download smoke check. |
| 4 – Payment APIs | Manual entries, confirm/reject/reverse, UPI app-chooser links, Razorpay links/cancel, signed webhooks. | Payment/UPI/webhook/idempotency tests. |
| 5 – WhatsApp | Approved template payloads, consent, opt-out, statuses, idempotency, history, rate limits. | WhatsApp tests with deterministic provider mocks. |
| 6 – Reminders | Eligibility engine, preview tokens, bulk send, quiet hours, scheduler jobs. | Reminder/scheduler tests. |
| 7 – Reports/audit | Dashboard, report tables/exports, audit logs, webhook events, scheduler runs. | Reports/settings/audit tests. |
| 8 – Frontend | Auth, dashboard, customers, payments, reminders, messages, imports, reports, settings, system pages. | `npm run build`; authenticated UI walkthrough with no console errors. |
| 9 – Deployment | Dockerfile, Compose, Render Blueprint, `.env.example`, `.gitignore`. | Files validated by inspection; Docker unavailable locally. |
| 10 – Final hardening | Decimal-only exports, UTC datetime normalization, masked settings, UPI app chooser, final tests/docs. | 67 tests pass; migration check clean; single-service static/API serving returns 200. |

## 12. Tests and verification results

Run from `backend/`:

```powershell
$env:DATABASE_URL = 'sqlite:///./pytest_essvee.db'
$env:ENABLE_SCHEDULER = 'false'
$env:SECRET_KEY = 'pytest-secret-key-do-not-use-in-production'
& "..\.venv\Scripts\python.exe" -m pytest tests -q --disable-warnings
```

Result in this build environment: **67 passed** (including customers, Excel import/reconciliation, payments, UPI deep links/redirect/app chooser, signed webhooks and idempotency, link reuse/cancel, WhatsApp templates/consent/status/opt-out, bulk preview tokens, scheduler jobs, auth, settings masking, reports and audit).

Migration check:

```powershell
$env:DATABASE_URL = 'sqlite:///./alembic_check.db'
& "..\.venv\Scripts\python.exe" -m alembic upgrade head
& "..\.venv\Scripts\python.exe" -m alembic check
```

Result: migration `638b1677c532` applies cleanly; `No new upgrade operations detected`.

Frontend build:

```powershell
cd frontend
npm install --no-audit --no-fund
npm run build
```

Result: build succeeded (2,061 modules transformed).

Manual smoke checks completed locally: login, dashboard with live data, customer create/history/adjustment/exclusion, Excel template, reports, settings, scheduler/audit views, bulk preview, backend `smoke_test.py` covering UPI setup check → UPI link → HTTPS `/pay/<token>` redirect containing `upi://pay` → UPI payment recorded from UTR → confirm → balance recompute. `confirm` reports `message_status=FAILED` when WhatsApp credentials are absent, while the financial confirmation still commits — by design. Browser verification confirmed the new **Settings → UPI** form renders and the `/pay/<token>` page attempts a `upi://pay?pa=…&pn=…&cu=INR` navigation while showing a visible **Open UPI apps** fallback and the customer-enters-amount note. No browser console errors were observed during the authenticated UI walkthrough.

### Acceptance workflow coverage

1. Admin login and session guard.
2. Customer create/edit with normalized WhatsApp number.
3. Excel template/download, preview, validation and import.
4. Balance adjustments with append-only ledger.
5. UPI link generation/reuse/cancel; Pay Now opens `https://…/pay/<token>` which launches the UPI app chooser with a customer-entered amount.
6. Approved-template reminder with Pay Now URL.
7. Consent and opt-out enforcement.
8. Bulk preview counts and explicit confirmation token.
9. UPI UTR recorded as a pending payment; Razorpay signed webhook ingestion and idempotency (when Razorpay is selected).
10. Manual payment entry awaiting confirmation.
11. Explicit admin confirm/reject/reverse.
12. Dashboard and collections/outstanding reports.
13. Message history and status callbacks.
14. Scheduler runs and audit/webhook views.
15. Settings/secrets configuration with actionable missing-credential errors.
16. Production build and single-service static+API serving.

## 13. Known limitations

- Real Meta and Razorpay calls were not executed because no live credentials were available; provider-facing code is tested with deterministic mocks and signed webhook payloads. Real approval and delivery depend on your accounts.
- UPI app chooser works on phones with a UPI app installed. Desktop browsers cannot complete a `upi://pay` link; they show the URI and instructions instead. Meta in-app browser behavior varies by platform, so the redirect page also provides a visible **Open UPI apps** button as a fallback.
- UPI deep links cannot verify or notify payments. The customer must share the UTR and an administrator must record/confirm it.
- Docker was unavailable locally; the Compose/Render files were not run here.
- The bootstrap admin password is a documented default and must be changed after first login.
- `get_settings()` is cached at process start; environment changes require an application restart.
- Payment reconciliation fetches link/payment status on a schedule; extremely delayed provider webhooks still require the admin review flow.
- This is a single-tenant deployment. Multi-tenant isolation is not implemented.

## 14. Security notes

- JWT bearer tokens, bcrypt password hashing, rate-limited login.
- Fernet-encrypted provider secrets; masked settings responses.
- HMAC verification for every incoming provider webhook.
- CORS restricted to configured origins; no tokens in frontend bundles.
- No raw card/UPI data is accepted or stored.
- All financial transitions write audit logs and ledger entries.
