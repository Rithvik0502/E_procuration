# Kisan Setu — Smart e-Procurement Platform
### SIH Problem Statement 26032 prototype

A working prototype of the solution: farmer registration, slot booking, a
live token queue, procurement tracking, and payment tracking, plus a
procurement-centre (admin) console to run the queue.

## What's included

| Layer | Tech | Notes |
|---|---|---|
| Frontend | HTML + CSS + JavaScript (Jinja templates) | No build step — served directly by Flask |
| Backend | Python + Flask | `app.py` |
| Database | SQLite | `procurement.db`, created automatically on first run. Swap for MySQL/Firebase for production — see "Moving to production" below |
| Live queue updates | JS polling `/api/queue-status` every 8s | Can be swapped for WebSockets later |
| Notifications | In-app (stored in the `notifications` table, shown on the dashboard) | SMS/WhatsApp is future scope, as in the original proposal |

## Running it

```bash
cd sih_procurement
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**.

- Farmers: register at `/register`, then log in at `/login` with Registration ID + name + mobile.
- Procurement centre staff: `/admin/login` — demo credentials `admin` / `admin123`.

The database and three days of demo procurement slots (9–11, 11:15–1:15, 3–5) are created automatically the first time the app runs.

## Database schema

```
farmers        reg_id (PK), name, mobile, aadhaar, village, crop, quantity, created_at
slots          id (PK), slot_date, start_time, end_time, capacity
bookings       id (PK), reg_id (FK), slot_id (FK), token_number, status, created_at
                 status: waiting -> serving -> completed
procurement    id (PK), booking_id (FK), reg_id, crop, quantity, procurement_date, status
payments       id (PK), procurement_id (FK), reg_id, amount, status, transaction_ref, payment_date
                 status: processing -> completed
notifications  id (PK), reg_id, message, created_at, is_read
```

This maps directly onto the farmer journey in the proposal:
`register -> login -> book slot -> get token -> track live queue -> arrive -> procurement -> payment`.

## How the smart queue works

For a farmer's booking, the server counts other bookings in the same slot
that are still `waiting` and were created earlier (`id < your booking id`).
That count is "farmers before you." Estimated wait = farmers before you ×
average processing time per farmer (5 minutes, configurable via
`AVG_PROCESSING_MINUTES` in `app.py`). "Currently serving" is whichever
booking in that slot has status `serving`.

Centre staff move the queue forward with one button — **Call next token** —
which completes whoever is currently serving and promotes the next
`waiting` booking to `serving`, triggering a notification to that farmer.

## Admin workflow

1. Staff log in and pick a time slot for the day.
2. **Call next token** advances the queue.
3. When a farmer is being served, staff enter the accepted quantity and
   rate per KG and click **Accept & procure** — this creates the
   procurement record and a payment record (status: processing) in one step.
4. **Mark payment complete** generates a transaction reference and flips
   the payment to completed — the farmer sees this instantly on their
   dashboard.

## Moving to production

This prototype uses SQLite so it runs anywhere with zero setup. For a real
deployment:

- **Database:** swap SQLite for MySQL (or Firebase if you want managed
  hosting + built-in auth) — the schema above maps directly to MySQL
  tables with minimal changes.
- **Notifications:** replace the in-app notification table with an
  SMS/WhatsApp API (e.g. Twilio, Gupshup, or the government's own SMS
  gateway) triggered at the same points in `app.py` where `notify()` is
  currently called.
- **Auth:** replace the name+mobile login check with OTP verification
  over SMS for stronger identity assurance.
- **Mobile app:** the `/api/queue-status` endpoint already returns JSON,
  so an Android/iOS or Flutter app can reuse the same Flask backend
  without changes — point it at the same API routes used by the web
  dashboard.
- **Scale:** add an index on `bookings(slot_id, status)` and consider
  moving live queue updates to WebSockets (Flask-SocketIO) once farmer
  volume grows.
