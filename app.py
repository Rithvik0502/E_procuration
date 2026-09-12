"""
Smart e-Procurement Platform for Farmers (SIH PS 26032)
Flask + SQLite prototype.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000
"""

import sqlite3
import random
import string
from datetime import datetime, date, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify, flash, g
)

app = Flask(__name__)
app.secret_key = "sih-2026-demo-secret-key"  # change for real deployment

DB_PATH = "procurement.db"
AVG_PROCESSING_MINUTES = 5   # used for wait-time estimate
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"   # demo only

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS farmers (
            reg_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mobile TEXT NOT NULL,
            aadhaar TEXT NOT NULL UNIQUE,
            village TEXT NOT NULL,
            crop TEXT NOT NULL,
            quantity REAL NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            capacity INTEGER NOT NULL DEFAULT 40
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reg_id TEXT NOT NULL,
            slot_id INTEGER NOT NULL,
            token_number TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'waiting',
            created_at TEXT NOT NULL,
            FOREIGN KEY(reg_id) REFERENCES farmers(reg_id),
            FOREIGN KEY(slot_id) REFERENCES slots(id)
        );

        CREATE TABLE IF NOT EXISTS procurement (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER NOT NULL,
            reg_id TEXT NOT NULL,
            crop TEXT NOT NULL,
            quantity REAL NOT NULL,
            procurement_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'accepted',
            created_at TEXT NOT NULL,
            FOREIGN KEY(booking_id) REFERENCES bookings(id)
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            procurement_id INTEGER NOT NULL,
            reg_id TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            transaction_ref TEXT,
            payment_date TEXT,
            FOREIGN KEY(procurement_id) REFERENCES procurement(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reg_id TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()

    # Seed a few slots for today and tomorrow if none exist
    cur.execute("SELECT COUNT(*) FROM slots")
    if cur.fetchone()[0] == 0:
        default_times = [
            ("09:00", "11:00"),
            ("11:15", "13:15"),
            ("15:00", "17:00"),
        ]
        for day_offset in range(0, 3):
            d = (date.today() + timedelta(days=day_offset)).isoformat()
            for start, end in default_times:
                cur.execute(
                    "INSERT INTO slots (slot_date, start_time, end_time, capacity) VALUES (?,?,?,?)",
                    (d, start, end, 40),
                )
        conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def generate_reg_id(db):
    cur = db.execute("SELECT COUNT(*) FROM farmers")
    n = cur.fetchone()[0] + 1
    return f"REG{n:02d}"


def generate_token(db, slot_id, slot_date):
    """Token like A2608: letter block + day-of-month + running sequence for that slot's day."""
    day_part = datetime.fromisoformat(slot_date).strftime("%d")
    cur = db.execute(
        """SELECT COUNT(*) FROM bookings b JOIN slots s ON b.slot_id = s.id
           WHERE s.slot_date = ?""",
        (slot_date,),
    )
    seq = cur.fetchone()[0] + 1
    return f"A{day_part}{seq:02d}"


def notify(db, reg_id, message):
    db.execute(
        "INSERT INTO notifications (reg_id, message, created_at) VALUES (?,?,?)",
        (reg_id, message, datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()


def current_farmer():
    reg_id = session.get("reg_id")
    if not reg_id:
        return None
    db = get_db()
    return db.execute("SELECT * FROM farmers WHERE reg_id = ?", (reg_id,)).fetchone()


def queue_snapshot(db, booking):
    """Compute currently-serving token, farmers ahead, and ETA for a booking."""
    slot_id = booking["slot_id"]
    serving = db.execute(
        "SELECT token_number FROM bookings WHERE slot_id=? AND status='serving' "
        "ORDER BY id LIMIT 1",
        (slot_id,),
    ).fetchone()
    ahead = db.execute(
        "SELECT COUNT(*) FROM bookings WHERE slot_id=? AND status='waiting' AND id < ?",
        (slot_id, booking["id"]),
    ).fetchone()[0]
    eta = ahead * AVG_PROCESSING_MINUTES
    return {
        "serving_token": serving["token_number"] if serving else "—",
        "ahead": ahead,
        "eta_minutes": eta,
    }


# ---------------------------------------------------------------------------
# Farmer-facing routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        db = get_db()
        name = request.form["name"].strip()
        mobile = request.form["mobile"].strip()
        aadhaar = request.form["aadhaar"].strip()
        village = request.form["village"].strip()
        crop = request.form["crop"].strip()
        quantity = request.form["quantity"].strip()

        if not (name and mobile and aadhaar and village and crop and quantity):
            flash("Please fill in every field.", "error")
            return render_template("register.html")

        existing = db.execute(
            "SELECT * FROM farmers WHERE aadhaar = ?", (aadhaar,)
        ).fetchone()
        if existing:
            flash(
                f"This Aadhaar number is already registered as {existing['reg_id']}. "
                f"Please log in instead of registering again.",
                "error",
            )
            return redirect(url_for("login"))

        reg_id = generate_reg_id(db)
        try:
            db.execute(
                """INSERT INTO farmers (reg_id, name, mobile, aadhaar, village, crop, quantity, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (reg_id, name, mobile, aadhaar, village, crop, float(quantity),
                 datetime.now().isoformat(timespec="seconds")),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            flash("This Aadhaar number is already registered. Please log in instead.", "error")
            return redirect(url_for("login"))
        notify(db, reg_id, f"Welcome {name}! Your Registration ID is {reg_id}.")
        session["reg_id"] = reg_id
        flash(f"Registration successful. Your Registration ID is {reg_id} — please save it.", "success")
        return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        reg_id = request.form["reg_id"].strip().upper()
        name = request.form["name"].strip()
        mobile = request.form["mobile"].strip()

        farmer = db.execute(
            "SELECT * FROM farmers WHERE reg_id=? AND mobile=?",
            (reg_id, mobile),
        ).fetchone()

        if farmer and farmer["name"].lower() == name.lower():
            session["reg_id"] = reg_id
            return redirect(url_for("dashboard"))
        flash("No matching farmer found. Check your Registration ID, name and mobile number.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    farmer = current_farmer()
    if not farmer:
        return redirect(url_for("login"))
    db = get_db()

    booking = db.execute(
        """SELECT b.*, s.slot_date, s.start_time, s.end_time FROM bookings b
           JOIN slots s ON b.slot_id = s.id
           WHERE b.reg_id=? ORDER BY b.id DESC LIMIT 1""",
        (farmer["reg_id"],),
    ).fetchone()

    queue_info = None
    if booking and booking["status"] == "waiting":
        queue_info = queue_snapshot(db, booking)

    procurement = None
    payment = None
    if booking:
        procurement = db.execute(
            "SELECT * FROM procurement WHERE booking_id=? ORDER BY id DESC LIMIT 1",
            (booking["id"],),
        ).fetchone()
        if procurement:
            payment = db.execute(
                "SELECT * FROM payments WHERE procurement_id=? ORDER BY id DESC LIMIT 1",
                (procurement["id"],),
            ).fetchone()

    notifications = db.execute(
        "SELECT * FROM notifications WHERE reg_id=? ORDER BY id DESC LIMIT 8",
        (farmer["reg_id"],),
    ).fetchall()

    return render_template(
        "dashboard.html",
        farmer=farmer,
        booking=booking,
        queue_info=queue_info,
        procurement=procurement,
        payment=payment,
        notifications=notifications,
    )


@app.route("/book-slot", methods=["GET", "POST"])
def book_slot():
    farmer = current_farmer()
    if not farmer:
        return redirect(url_for("login"))
    db = get_db()

    if request.method == "POST":
        slot_id = request.form["slot_id"]
        slot = db.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()

        booked_count = db.execute(
            "SELECT COUNT(*) FROM bookings WHERE slot_id=?", (slot_id,)
        ).fetchone()[0]
        if booked_count >= slot["capacity"]:
            flash("That slot is full. Please choose another.", "error")
            return redirect(url_for("book_slot"))

        token = generate_token(db, slot_id, slot["slot_date"])
        db.execute(
            """INSERT INTO bookings (reg_id, slot_id, token_number, status, created_at)
               VALUES (?,?,?, 'waiting', ?)""",
            (farmer["reg_id"], slot_id, token, datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
        notify(
            db, farmer["reg_id"],
            f"Slot booked for {slot['slot_date']} {slot['start_time']}-{slot['end_time']}. Your token is {token}.",
        )
        flash(f"Slot booked! Your token number is {token}.", "success")
        return redirect(url_for("dashboard"))

    slots = db.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM bookings b WHERE b.slot_id = s.id) AS booked_count
           FROM slots s WHERE s.slot_date >= ? ORDER BY s.slot_date, s.start_time""",
        (date.today().isoformat(),),
    ).fetchall()
    return render_template("book_slot.html", slots=slots, farmer=farmer)


@app.route("/api/queue-status")
def api_queue_status():
    """Polled by the dashboard page to refresh live queue info without a full reload."""
    farmer = current_farmer()
    if not farmer:
        return jsonify({"error": "not logged in"}), 401
    db = get_db()
    booking = db.execute(
        "SELECT * FROM bookings WHERE reg_id=? ORDER BY id DESC LIMIT 1",
        (farmer["reg_id"],),
    ).fetchone()
    if not booking:
        return jsonify({"has_booking": False})
    if booking["status"] != "waiting":
        return jsonify({"has_booking": True, "status": booking["status"], "token": booking["token_number"]})
    info = queue_snapshot(db, booking)
    return jsonify({
        "has_booking": True,
        "status": "waiting",
        "token": booking["token_number"],
        "serving_token": info["serving_token"],
        "ahead": info["ahead"],
        "eta_minutes": info["eta_minutes"],
    })


# ---------------------------------------------------------------------------
# Admin (Procurement Centre) routes
# ---------------------------------------------------------------------------

def admin_required():
    return session.get("is_admin", False)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if (request.form["username"] == ADMIN_USERNAME
                and request.form["password"] == ADMIN_PASSWORD):
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))


@app.route("/admin")
def admin_dashboard():
    if not admin_required():
        return redirect(url_for("admin_login"))
    db = get_db()
    today = date.today().isoformat()
    slot_id = request.args.get("slot_id")

    slots_today = db.execute(
        "SELECT * FROM slots WHERE slot_date=? ORDER BY start_time", (today,)
    ).fetchall()

    if not slot_id and slots_today:
        slot_id = slots_today[0]["id"]

    bookings = []
    if slot_id:
        bookings = db.execute(
            """SELECT b.*, f.name, f.village, f.crop, f.quantity, f.mobile,
                      p.id AS procurement_id, p.status AS procurement_status,
                      pay.id AS payment_id, pay.status AS payment_status, pay.amount AS payment_amount
               FROM bookings b
               JOIN farmers f ON b.reg_id = f.reg_id
               LEFT JOIN procurement p ON p.booking_id = b.id
               LEFT JOIN payments pay ON pay.procurement_id = p.id
               WHERE b.slot_id=? ORDER BY b.id""",
            (slot_id,),
        ).fetchall()

    return render_template(
        "admin_dashboard.html",
        slots_today=slots_today,
        selected_slot_id=int(slot_id) if slot_id else None,
        bookings=bookings,
    )


@app.route("/admin/call-next/<int:slot_id>", methods=["POST"])
def admin_call_next(slot_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    db = get_db()
    # mark any currently serving token in this slot as completed
    db.execute(
        "UPDATE bookings SET status='completed' WHERE slot_id=? AND status='serving'",
        (slot_id,),
    )
    nxt = db.execute(
        "SELECT * FROM bookings WHERE slot_id=? AND status='waiting' ORDER BY id LIMIT 1",
        (slot_id,),
    ).fetchone()
    if nxt:
        db.execute("UPDATE bookings SET status='serving' WHERE id=?", (nxt["id"],))
        db.commit()
        notify(db, nxt["reg_id"], f"It's your turn now! Token {nxt['token_number']} is being served.")
    else:
        db.commit()
    return redirect(url_for("admin_dashboard", slot_id=slot_id))


@app.route("/admin/accept/<int:booking_id>", methods=["POST"])
def admin_accept(booking_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
    farmer = db.execute("SELECT * FROM farmers WHERE reg_id=?", (booking["reg_id"],)).fetchone()

    quantity = float(request.form.get("quantity", farmer["quantity"]))
    rate = float(request.form.get("rate", 20))  # per KG, demo default

    db.execute("UPDATE bookings SET status='completed' WHERE id=?", (booking_id,))
    cur = db.execute(
        """INSERT INTO procurement (booking_id, reg_id, crop, quantity, procurement_date, status, created_at)
           VALUES (?,?,?,?,?, 'accepted', ?)""",
        (booking_id, farmer["reg_id"], farmer["crop"], quantity, date.today().isoformat(),
         datetime.now().isoformat(timespec="seconds")),
    )
    procurement_id = cur.lastrowid
    amount = round(quantity * rate, 2)
    db.execute(
        """INSERT INTO payments (procurement_id, reg_id, amount, status)
           VALUES (?,?,?, 'processing')""",
        (procurement_id, farmer["reg_id"], amount),
    )
    db.commit()
    notify(db, farmer["reg_id"],
           f"Your crop ({quantity} KG {farmer['crop']}) has been procured. Payment of Rs.{amount} is processing.")
    return redirect(url_for("admin_dashboard", slot_id=booking["slot_id"]))


@app.route("/admin/complete-payment/<int:payment_id>", methods=["POST"])
def admin_complete_payment(payment_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    db = get_db()
    ref = "TXN" + "".join(random.choices(string.digits, k=8))
    db.execute(
        "UPDATE payments SET status='completed', transaction_ref=?, payment_date=? WHERE id=?",
        (ref, date.today().isoformat(), payment_id),
    )
    db.commit()
    payment = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    notify(db, payment["reg_id"],
           f"Payment of Rs.{payment['amount']} completed. Reference: {ref}.")
    slot_id = request.form.get("slot_id", "")
    return redirect(url_for("admin_dashboard", slot_id=slot_id))


init_db()  # runs whether started via "python app.py" or a production server like gunicorn

if __name__ == "__main__":
    app.run(debug=True)
