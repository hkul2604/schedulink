import os
from flask import Flask, request, jsonify, render_template
import psycopg2
import psycopg2.pool
import psycopg2.errors
import hashlib

app = Flask(__name__)

# --- Connection settings ---
# Set these via environment variables, e.g.:
#   DATABASE_URL=postgresql://user:password@localhost:5432/appointments
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://schedulink_postgresql_user:rVNtIjsdUQrj7XIkMEy2wtKcxRikV5NG@dpg-da1lkerm8hqs73bbblhg-a.oregon-postgres.render.com/schedulink_postgresql"
)

# Simple connection pool (min 1, max 10 connections). Adjust as needed.
pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)

# Shared secret for admin-only endpoints (e.g. setting a professional's access
# tier). Must be set explicitly — no default — so the endpoint is disabled
# rather than protected by a guessable value if it's left unconfigured.
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")


def init_db():
    conn = pool.getconn()
    try:
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT,
            role TEXT,
            phone TEXT UNIQUE,
            email TEXT UNIQUE,
            password TEXT,
            access_type TEXT NOT NULL DEFAULT 'free' CHECK (access_type IN ('free','paid','premium'))
        )''')
        # Migration for databases created before this column existed. Only
        # meaningful for customer rows — professionals' tier lives in the
        # professionals table instead, so this is simply unused for them.
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS access_type TEXT NOT NULL DEFAULT 'free'")
        c.execute('''ALTER TABLE users DROP CONSTRAINT IF EXISTS users_access_type_check''')
        c.execute('''ALTER TABLE users ADD CONSTRAINT users_access_type_check
            CHECK (access_type IN ('free','paid','premium'))''')

        c.execute('''CREATE TABLE IF NOT EXISTS professionals (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE,
            business_type TEXT,
            establishment TEXT,
            city TEXT,
            pincode TEXT,
            access_type TEXT NOT NULL DEFAULT 'free' CHECK (access_type IN ('free','paid','premium'))
        )''')
        # Migrations for databases created before these columns existed.
        c.execute("ALTER TABLE professionals ADD COLUMN IF NOT EXISTS pincode TEXT")
        c.execute("ALTER TABLE professionals ADD COLUMN IF NOT EXISTS access_type TEXT NOT NULL DEFAULT 'free'")
        c.execute('''ALTER TABLE professionals DROP CONSTRAINT IF EXISTS professionals_access_type_check''')
        c.execute('''ALTER TABLE professionals ADD CONSTRAINT professionals_access_type_check
            CHECK (access_type IN ('free','paid','premium'))''')

        c.execute('''CREATE TABLE IF NOT EXISTS professional_slots (
            id SERIAL PRIMARY KEY,
            professional TEXT,
            date TEXT,
            slot TEXT,
            capacity INTEGER,
            booked_count INTEGER,
            status TEXT
        )''')
        # One-time cleanup for duplicate (professional,date,slot) rows created
        # before a uniqueness constraint existed, keeping the earliest of each.
        c.execute('''DELETE FROM professional_slots
                     WHERE id NOT IN (
                         SELECT MIN(id) FROM professional_slots
                         GROUP BY professional, date, slot
                     )''')
        c.execute("SELECT 1 FROM pg_constraint WHERE conname = 'professional_slots_unique'")
        if not c.fetchone():
            c.execute('''ALTER TABLE professional_slots ADD CONSTRAINT professional_slots_unique
                         UNIQUE (professional, date, slot)''')

        c.execute('''CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            customer_phone TEXT,
            customer_name TEXT,
            professional TEXT,
            slot TEXT,
            date TEXT,
            status TEXT
        )''')

        # One-way in-app notifications, sent when an appointment is requested
        # and either party is a premium user. recipient_id is the customer's
        # phone or the professional's name, matching how they're identified
        # everywhere else in this app (no real foreign keys anywhere here).
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            recipient_type TEXT NOT NULL CHECK (recipient_type IN ('customer','professional')),
            recipient_id TEXT NOT NULL,
            professional TEXT,
            customer_name TEXT,
            slot TEXT,
            date TEXT,
            body TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            is_read BOOLEAN NOT NULL DEFAULT false
        )''')

        conn.commit()
    finally:
        pool.putconn(conn)


init_db()


def query_db(q, args=(), one=False):
    conn = pool.getconn()
    try:
        c = conn.cursor()
        c.execute(q, args)
        # SELECT queries have a description; INSERT/UPDATE without RETURNING don't
        if c.description is not None:
            rv = c.fetchall()
        else:
            rv = []
        conn.commit()
        return (rv[0] if rv else None) if one else rv
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def execute_db(q, args=()):
    conn = pool.getconn()
    try:
        c = conn.cursor()
        c.execute(q, args)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/professional")
def professional_dashboard(): return render_template("professional.html")


@app.route("/customer")
def customer_dashboard(): return render_template("customer.html")


@app.route("/admin")
def admin_dashboard(): return render_template("admin.html")


# --- Professionals list ---
@app.route("/professionals")
def list_professionals():
    rows = query_db("SELECT name,business_type,establishment,city,pincode FROM professionals")
    return jsonify([{"name": r[0], "business_type": r[1], "establishment": r[2], "city": r[3], "pincode": r[4]} for r in rows])


# --- Registration/Login ---
@app.route("/register_professional", methods=["POST"])
def register_professional():
    d = request.json
    if not d.get("pincode"):
        return jsonify({"error": "Pin code is required"}), 400
    try:
        query_db("INSERT INTO users (name, role, email, password) VALUES (%s, 'professional', %s, %s)",
                 (d["name"], d["email"], hash_password(d["password"])))
    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "An account with this email already exists"}), 409
    query_db("INSERT INTO professionals (name,business_type,establishment,city,pincode) VALUES (%s,%s,%s,%s,%s) "
             "ON CONFLICT (name) DO NOTHING",
             (d["name"], d.get("business_type"), d.get("establishment"), d.get("city"), d.get("pincode")))
    return jsonify({"message": "Professional registered"})


@app.route("/login_professional", methods=["POST"])
def login_professional():
    d = request.json
    u = query_db(
        "SELECT * FROM users WHERE email=%s AND password=%s AND role='professional'",
        (d["email"], hash_password(d["password"])),
        one=True
    )
    if u:
        return jsonify({
            "message": "Login ok",
            "email": d.get("email"),  # safe dictionary access
            "name": u[1]              # name column from DB row
        })
    return jsonify({"error": "Invalid"}), 401


def authenticate_professional(email, password):
    """Look up the professional's own name from their login credentials.
    Callers must never trust a client-supplied name for identifying whose
    record to modify — always use the name this returns."""
    u = query_db(
        "SELECT name FROM users WHERE email=%s AND password=%s AND role='professional'",
        (email, hash_password(password)),
        one=True
    )
    return u[0] if u else None


@app.route("/update_professional", methods=["POST"])
def update_professional():
    d = request.json
    name = authenticate_professional(d.get("email"), d.get("password"))
    if not name:
        return jsonify({"error": "Invalid"}), 401
    if not d.get("pincode"):
        return jsonify({"error": "Pin code is required"}), 400
    query_db(
        "UPDATE professionals SET business_type=%s, establishment=%s, city=%s, pincode=%s WHERE name=%s",
        (d.get("business_type"), d.get("establishment"), d.get("city"), d.get("pincode"), name)
    )
    row = query_db(
        "SELECT name,business_type,establishment,city,pincode FROM professionals WHERE name=%s",
        (name,), one=True
    )
    return jsonify({
        "message": "Profile updated",
        "name": row[0], "business_type": row[1], "establishment": row[2], "city": row[3], "pincode": row[4]
    })


# --- Admin: professional access tier ---
# Access type (free/paid/premium) is set by an admin only — professionals
# cannot self-select their own tier. There's no admin role/session system in
# this app yet, so this is gated by a shared secret instead.
@app.route("/admin/professionals", methods=["POST"])
def admin_list_professionals():
    d = request.json
    if not ADMIN_SECRET or d.get("admin_secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    rows = query_db("SELECT name,business_type,establishment,city,pincode,access_type FROM professionals ORDER BY name")
    return jsonify([
        {"name": r[0], "business_type": r[1], "establishment": r[2], "city": r[3], "pincode": r[4], "access_type": r[5]}
        for r in rows
    ])


@app.route("/admin/set_access_type", methods=["POST"])
def admin_set_access_type():
    d = request.json
    if not ADMIN_SECRET or d.get("admin_secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    access_type = d.get("access_type")
    if access_type not in ("free", "paid", "premium"):
        return jsonify({"error": "access_type must be one of: free, paid, premium"}), 400
    name = d.get("name")
    existing = query_db("SELECT name FROM professionals WHERE name=%s", (name,), one=True)
    if not existing:
        return jsonify({"error": "Professional not found"}), 404
    query_db("UPDATE professionals SET access_type=%s WHERE name=%s", (access_type, name))
    return jsonify({"name": name, "access_type": access_type})


# --- Admin: customer access tier ---
# Mirrors the professional access-tier endpoints above, but customers only
# ever live in the users table (role='customer'), keyed by phone.
@app.route("/admin/customers", methods=["POST"])
def admin_list_customers():
    d = request.json
    if not ADMIN_SECRET or d.get("admin_secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    rows = query_db(
        "SELECT name,phone,access_type FROM users WHERE role='customer' ORDER BY name"
    )
    return jsonify([{"name": r[0], "phone": r[1], "access_type": r[2]} for r in rows])


@app.route("/admin/set_customer_access_type", methods=["POST"])
def admin_set_customer_access_type():
    d = request.json
    if not ADMIN_SECRET or d.get("admin_secret") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    access_type = d.get("access_type")
    if access_type not in ("free", "paid", "premium"):
        return jsonify({"error": "access_type must be one of: free, paid, premium"}), 400
    phone = d.get("phone")
    existing = query_db("SELECT phone FROM users WHERE phone=%s AND role='customer'", (phone,), one=True)
    if not existing:
        return jsonify({"error": "Customer not found"}), 404
    query_db("UPDATE users SET access_type=%s WHERE phone=%s AND role='customer'", (access_type, phone))
    return jsonify({"phone": phone, "access_type": access_type})


@app.route("/register_customer", methods=["POST"])
def register_customer():
    d = request.json
    try:
        query_db("INSERT INTO users (name, role, phone, password) VALUES (%s, 'customer', %s, %s)",
                 (d["name"], d["phone"], hash_password(d["password"])))
    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "An account with this phone number already exists"}), 409
    return jsonify({"message": "Customer registered"})


@app.route("/login_customer", methods=["POST"])
def login_customer():
    phone = request.json.get("phone")
    u = query_db("SELECT * FROM users WHERE phone=%s AND role='customer'", (phone,), one=True)
    if u: return jsonify({"message": "Login ok", "phone": phone, "name": u[1]})
    return jsonify({"error": "Customer not found"}), 404


# --- Slot Management ---
@app.route("/add_slots", methods=["POST"])
def add_slots():
    d = request.json
    added, skipped = 0, 0
    for s in d["slots"]:
        inserted = query_db(
            "INSERT INTO professional_slots (professional,date,slot,capacity,booked_count,status) "
            "VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (professional,date,slot) DO NOTHING "
            "RETURNING id",
            (d["name"], d["date"], s, d["capacity"], 0, "Available")
        )
        if inserted:
            added += 1
        else:
            skipped += 1
    message = f"Added {added} new slot(s) for {d['name']} on {d['date']}"
    if skipped:
        message += f"; {skipped} already existed and were left unchanged"
    return jsonify({"message": message, "added": added, "skipped": skipped})


@app.route("/slots/<professional>/<date>")
def view_slots(professional, date):
    rows = query_db("SELECT slot,capacity,booked_count,status FROM professional_slots "
                     "WHERE professional=%s AND date=%s", (professional, date))
    return jsonify([{"slot": r[0], "capacity": r[1], "booked": r[2], "status": r[3]} for r in rows])


@app.route("/last_slots/<professional>")
def last_slots(professional):
    """Most recent date this professional added slots for, so the Add Slots
    screen can offer to reuse that day's slot times/capacity on a new date."""
    latest = query_db(
        "SELECT date FROM professional_slots WHERE professional=%s ORDER BY date DESC LIMIT 1",
        (professional,), one=True
    )
    if not latest:
        return jsonify({"date": None, "slots": [], "capacity": None})
    date = latest[0]
    rows = query_db(
        "SELECT slot,capacity FROM professional_slots WHERE professional=%s AND date=%s ORDER BY slot",
        (professional, date)
    )
    return jsonify({
        "date": date,
        "slots": [r[0] for r in rows],
        "capacity": rows[0][1] if rows else None
    })


# --- Booking ---
# One message pair per lifecycle event a premium user should be notified
# about. {cname}/{professional}/{slot}/{date} are filled in per appointment.
APPOINTMENT_EVENT_MESSAGES = {
    "requested": {
        "professional": "New appointment request from {cname} for {slot} on {date}.",
        "customer": "Your appointment request with {professional} for {slot} on {date} has been sent.",
    },
    "accepted": {
        "professional": "You accepted {cname}'s appointment for {slot} on {date}.",
        "customer": "Your appointment with {professional} for {slot} on {date} was accepted.",
    },
    "cancelled": {
        "professional": "{cname} cancelled their appointment for {slot} on {date}.",
        "customer": "Your appointment with {professional} for {slot} on {date} was cancelled.",
    },
    "rescheduled": {
        "professional": "{cname} rescheduled their appointment to {slot} on {date}.",
        "customer": "Your appointment with {professional} was rescheduled to {slot} on {date}.",
    },
}


def notify_appointment_event(phone, cname, professional, slot, date, event):
    """Send a one-way in-app notification to both sides of an appointment
    lifecycle event, but only when the customer or the professional is
    premium. `event` is one of APPOINTMENT_EVENT_MESSAGES' keys."""
    cust_tier = query_db(
        "SELECT access_type FROM users WHERE phone=%s AND role='customer'", (phone,), one=True
    )
    pro_tier = query_db(
        "SELECT access_type FROM professionals WHERE name=%s", (professional,), one=True
    )
    is_premium = (cust_tier and cust_tier[0] == "premium") or (pro_tier and pro_tier[0] == "premium")
    if not is_premium:
        return
    templates = APPOINTMENT_EVENT_MESSAGES[event]
    fields = {"cname": cname, "professional": professional, "slot": slot, "date": date}
    query_db(
        "INSERT INTO messages (recipient_type,recipient_id,professional,customer_name,slot,date,body) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        ("professional", professional, professional, cname, slot, date,
         templates["professional"].format(**fields))
    )
    query_db(
        "INSERT INTO messages (recipient_type,recipient_id,professional,customer_name,slot,date,body) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        ("customer", phone, professional, cname, slot, date,
         templates["customer"].format(**fields))
    )


@app.route("/book", methods=["POST"])
def book(event="requested"):
    d = request.json
    cust = query_db("SELECT name FROM users WHERE phone=%s AND role='customer'", (d["phone"],), one=True)
    if not cust: return jsonify({"error": "Customer not registered"}), 400
    # prevent double booking
    existing = query_db("SELECT * FROM appointments WHERE customer_phone=%s AND professional=%s "
                         "AND date=%s AND slot=%s",
                         (d["phone"], d["professional"], d["date"], d["slot"]), one=True)
    if existing: return jsonify({"error": "Already booked this slot"}), 400
    cname = cust[0]
    query_db("INSERT INTO appointments (customer_phone,customer_name,professional,slot,date,status) "
             "VALUES (%s,%s,%s,%s,%s,%s)",
             (d["phone"], cname, d["professional"], d["slot"], d["date"], "Pending"))
    slot = query_db("SELECT capacity,booked_count FROM professional_slots "
                     "WHERE professional=%s AND date=%s AND slot=%s",
                     (d["professional"], d["date"], d["slot"]), one=True)
    if slot:
        cap, booked = slot; booked += 1
        status = "Available"
        if booked >= cap: status = "Full"
        elif booked >= cap * 0.7: status = "Filling Fast"
        query_db("UPDATE professional_slots SET booked_count=%s,status=%s "
                 "WHERE professional=%s AND date=%s AND slot=%s",
                 (booked, status, d["professional"], d["date"], d["slot"]))
    notify_appointment_event(d["phone"], cname, d["professional"], d["slot"], d["date"], event)
    return jsonify({"message": "Appointment requested", "customer_name": cname})


# --- Reschedule ---
@app.route("/reschedule", methods=["POST"])
def reschedule():
    d = request.json
    # cancel old
    query_db("UPDATE appointments SET status='Cancelled' WHERE customer_phone=%s AND professional=%s "
             "AND slot=%s AND date=%s",
             (d["phone"], d["professional"], d["old_slot"], d["old_date"]))
    # free old seat
    slot = query_db("SELECT capacity,booked_count FROM professional_slots "
                     "WHERE professional=%s AND date=%s AND slot=%s",
                     (d["professional"], d["old_date"], d["old_slot"]), one=True)
    if slot:
        cap, booked = slot; booked = max(0, booked - 1)
        status = "Available"
        if booked >= cap: status = "Full"
        elif booked >= cap * 0.7: status = "Filling Fast"
        query_db("UPDATE professional_slots SET booked_count=%s,status=%s "
                 "WHERE professional=%s AND date=%s AND slot=%s",
                 (booked, status, d["professional"], d["old_date"], d["old_slot"]))
    # book new
    return book(event="rescheduled")


# --- Messages ---
# One-way in-app notifications only (no replies). Fetching a recipient's
# messages also marks them read, since there's no separate UI action for it.
@app.route("/messages/customer/<phone>")
def customer_messages(phone):
    rows = query_db(
        "SELECT id,professional,customer_name,slot,date,body,created_at,is_read "
        "FROM messages WHERE recipient_type='customer' AND recipient_id=%s ORDER BY created_at DESC",
        (phone,)
    )
    query_db(
        "UPDATE messages SET is_read=true WHERE recipient_type='customer' AND recipient_id=%s",
        (phone,)
    )
    return jsonify([
        {"id": r[0], "professional": r[1], "customer_name": r[2], "slot": r[3], "date": r[4],
         "body": r[5], "created_at": r[6].isoformat(), "was_read": r[7]}
        for r in rows
    ])


@app.route("/messages/professional/<name>")
def professional_messages(name):
    rows = query_db(
        "SELECT id,professional,customer_name,slot,date,body,created_at,is_read "
        "FROM messages WHERE recipient_type='professional' AND recipient_id=%s ORDER BY created_at DESC",
        (name,)
    )
    query_db(
        "UPDATE messages SET is_read=true WHERE recipient_type='professional' AND recipient_id=%s",
        (name,)
    )
    return jsonify([
        {"id": r[0], "professional": r[1], "customer_name": r[2], "slot": r[3], "date": r[4],
         "body": r[5], "created_at": r[6].isoformat(), "was_read": r[7]}
        for r in rows
    ])


# Unread counts, kept separate from the list endpoints above since those
# mark messages read as a side effect — this lets the UI show a badge
# before the customer/professional actually opens the Messages tab.
@app.route("/messages/customer/<phone>/unread_count")
def customer_unread_count(phone):
    row = query_db(
        "SELECT COUNT(*) FROM messages WHERE recipient_type='customer' AND recipient_id=%s AND is_read=false",
        (phone,), one=True
    )
    return jsonify({"unread": row[0] if row else 0})


@app.route("/messages/professional/<name>/unread_count")
def professional_unread_count(name):
    row = query_db(
        "SELECT COUNT(*) FROM messages WHERE recipient_type='professional' AND recipient_id=%s AND is_read=false",
        (name,), one=True
    )
    return jsonify({"unread": row[0] if row else 0})


# --- Appointments & Metrics ---
@app.route("/appointments/professional/<name>/<date>")
def appts(name, date):
    rows = query_db("SELECT customer_name,customer_phone,slot,status FROM appointments "
                     "WHERE professional=%s AND date=%s", (name, date))
    return jsonify([{"customer_name": r[0], "customer_phone": r[1], "slot": r[2], "status": r[3]} for r in rows])


@app.route("/metrics/<professional>/<date>")
def metrics(professional, date):
    rows = query_db("SELECT status FROM appointments WHERE professional=%s AND date=%s", (professional, date))
    m = {"Pending": 0, "Approved": 0, "Rejected": 0, "Cancelled": 0}
    for r in rows:
        if r[0] in m: m[r[0]] += 1
    slots = query_db("SELECT capacity,booked_count FROM professional_slots "
                      "WHERE professional=%s AND date=%s", (professional, date))
    m["Available Seats"] = sum(cap - booked for cap, booked in slots)
    return jsonify(m)


@app.route("/approve", methods=["POST"])
def approve():
    d = request.json
    appt = query_db("SELECT customer_name FROM appointments WHERE customer_phone=%s AND professional=%s "
                     "AND slot=%s AND date=%s",
                     (d["phone"], d["professional"], d["slot"], d["date"]), one=True)
    query_db("UPDATE appointments SET status='Approved' WHERE customer_phone=%s AND professional=%s "
             "AND slot=%s AND date=%s",
             (d["phone"], d["professional"], d["slot"], d["date"]))
    if appt:
        notify_appointment_event(d["phone"], appt[0], d["professional"], d["slot"], d["date"], "accepted")
    return jsonify({"message": "Approved"})


@app.route("/reject", methods=["POST"])
def reject():
    d = request.json
    query_db("UPDATE appointments SET status='Rejected' WHERE customer_phone=%s AND professional=%s "
             "AND slot=%s AND date=%s",
             (d["phone"], d["professional"], d["slot"], d["date"]))
    return jsonify({"message": "Rejected"})


@app.route("/cancel", methods=["POST"])
def cancel():
    d = request.json
    appt = query_db("SELECT customer_name FROM appointments WHERE customer_phone=%s AND professional=%s "
                     "AND slot=%s AND date=%s",
                     (d["phone"], d["professional"], d["slot"], d["date"]), one=True)
    query_db("UPDATE appointments SET status='Cancelled' WHERE customer_phone=%s AND professional=%s "
             "AND slot=%s AND date=%s",
             (d["phone"], d["professional"], d["slot"], d["date"]))
    # free seat
    slot = query_db("SELECT capacity,booked_count FROM professional_slots "
                     "WHERE professional=%s AND date=%s AND slot=%s",
                     (d["professional"], d["date"], d["slot"]), one=True)
    if slot:
        cap, booked = slot; booked = max(0, booked - 1)
        status = "Available"
        if booked >= cap: status = "Full"
        elif booked >= cap * 0.7: status = "Filling Fast"
        query_db("UPDATE professional_slots SET booked_count=%s,status=%s "
                 "WHERE professional=%s AND date=%s AND slot=%s",
                 (booked, status, d["professional"], d["date"], d["slot"]))
    if appt:
        notify_appointment_event(d["phone"], appt[0], d["professional"], d["slot"], d["date"], "cancelled")
    return jsonify({"message": "Cancelled"})


if __name__ == "__main__": app.run(debug=True)