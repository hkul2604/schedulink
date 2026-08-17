import os
from flask import Flask, request, jsonify, render_template
import psycopg2
import psycopg2.pool
import hashlib

app = Flask(__name__)

# --- Connection settings ---
# Set these via environment variables, e.g.:
#   DATABASE_URL=postgresql://user:password@localhost:5432/appointments
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/appointments"
)

# Simple connection pool (min 1, max 10 connections). Adjust as needed.
pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)


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
            password TEXT
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS professionals (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE,
            business_type TEXT,
            establishment TEXT,
            city TEXT
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS professional_slots (
            id SERIAL PRIMARY KEY,
            professional TEXT,
            date TEXT,
            slot TEXT,
            capacity INTEGER,
            booked_count INTEGER,
            status TEXT
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS appointments (
            id SERIAL PRIMARY KEY,
            customer_phone TEXT,
            customer_name TEXT,
            professional TEXT,
            slot TEXT,
            date TEXT,
            status TEXT
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
    finally:
        pool.putconn(conn)


def execute_db(q, args=()):
    conn = pool.getconn()
    try:
        c = conn.cursor()
        c.execute(q, args)
        conn.commit()
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


# --- Professionals list ---
@app.route("/professionals")
def list_professionals():
    rows = query_db("SELECT name,business_type,establishment,city FROM professionals")
    return jsonify([{"name": r[0], "business_type": r[1], "establishment": r[2], "city": r[3]} for r in rows])


# --- Registration/Login ---
@app.route("/register_professional", methods=["POST"])
def register_professional():
    d = request.json
    query_db("INSERT INTO users (name, role, email, password) VALUES (%s, 'professional', %s, %s)",
             (d["name"], d["email"], hash_password(d["password"])))
    # NOTE: original code referenced a "pin" column that doesn't exist in the
    # professionals table schema — dropped it here so the insert matches the schema.
    # Add a `pin TEXT` column to the CREATE TABLE above if you need it.
    query_db("INSERT INTO professionals (name,business_type,establishment,city) VALUES (%s,%s,%s,%s) "
             "ON CONFLICT (name) DO NOTHING",
             (d["name"], d.get("business_type"), d.get("establishment"), d.get("city")))
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


@app.route("/register_customer", methods=["POST"])
def register_customer():
    d = request.json
    query_db("INSERT INTO users (name, role, phone, password) VALUES (%s, 'customer', %s, %s)",
             (d["name"], d["phone"], hash_password(d["password"])))
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
    for s in d["slots"]:
        query_db("INSERT INTO professional_slots (professional,date,slot,capacity,booked_count,status) "
                 "VALUES (%s,%s,%s,%s,%s,%s)",
                 (d["name"], d["date"], s, d["capacity"], 0, "Available"))
    return jsonify({"message": f"Slots added for {d['name']} on {d['date']}"})


@app.route("/slots/<professional>/<date>")
def view_slots(professional, date):
    rows = query_db("SELECT slot,capacity,booked_count,status FROM professional_slots "
                     "WHERE professional=%s AND date=%s", (professional, date))
    return jsonify([{"slot": r[0], "capacity": r[1], "booked": r[2], "status": r[3]} for r in rows])


# --- Booking ---
@app.route("/book", methods=["POST"])
def book():
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
    return book()


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
    query_db("UPDATE appointments SET status='Approved' WHERE customer_phone=%s AND professional=%s "
             "AND slot=%s AND date=%s",
             (d["phone"], d["professional"], d["slot"], d["date"]))
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
    return jsonify({"message": "Cancelled"})


if __name__ == "__main__": app.run(debug=True)