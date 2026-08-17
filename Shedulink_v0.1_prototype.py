from flask import Flask, request, jsonify, render_template
import sqlite3, hashlib

app = Flask(__name__)
DB_NAME = "appointments_new.db"

# --- DB Setup ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        role TEXT,
        phone TEXT UNIQUE,
        email TEXT UNIQUE,
        password TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS professionals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS professional_slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        professional TEXT,
        date TEXT,
        slot TEXT,
        capacity INTEGER,
        booked_count INTEGER,
        status TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_phone TEXT,
        customer_name TEXT,
        professional TEXT,
        slot TEXT,
        date TEXT,
        status TEXT
    )''')

    conn.commit(); conn.close()

init_db()

def query_db(q, args=(), one=False):
    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
    c.execute(q, args); rv = c.fetchall()
    conn.commit(); conn.close()
    return (rv[0] if rv else None) if one else rv

def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()

# --- Serve Dashboards ---

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/professional")
def professional_dashboard(): return render_template("professional.html")

@app.route("/customer")
def customer_dashboard(): return render_template("customer.html")

# --- Registration/Login ---
@app.route("/register_customer", methods=["POST"])
def register_customer():
    d = request.json
    query_db("INSERT INTO users (name, role, phone, password) VALUES (?, 'customer', ?, ?)",
             (d["name"], d["phone"], hash_password(d["password"])))
    return jsonify({"message":"Customer registered"})

@app.route("/login_customer", methods=["POST"])
def login_customer():
    phone = request.json.get("phone")
    u = query_db("SELECT * FROM users WHERE phone=? AND role='customer'", (phone,), one=True)
    if u: return jsonify({"message":"Login ok","phone":phone,"name":u[1]})
    return jsonify({"error":"Customer not found"}),404

@app.route("/register_professional", methods=["POST"])
def register_professional():
    d = request.json
    query_db("INSERT INTO users (name, role, email, password) VALUES (?, 'professional', ?, ?)",
             (d["name"], d["email"], hash_password(d["password"])))
    query_db("INSERT OR IGNORE INTO professionals (name) VALUES (?)",(d["name"],))
    return jsonify({"message":"Professional registered"})

@app.route("/login_professional", methods=["POST"])
def login_professional():
    d = request.json
    u = query_db("SELECT * FROM users WHERE email=? AND password=? AND role='professional'",
                 (d["email"], hash_password(d["password"])), one=True)
    if u: return jsonify({"message":"Login ok","email":d["email"],"name":u[1]})
    return jsonify({"error":"Invalid"}),401

# --- Slot Management ---
@app.route("/add_slots", methods=["POST"])
def add_slots():
    d = request.json
    for s in d["slots"]:
        query_db("INSERT INTO professional_slots (professional,date,slot,capacity,booked_count,status) VALUES (?,?,?,?,?,?)",
                 (d["name"], d["date"], s, d["capacity"], 0, "Available"))
    return jsonify({"message":f"Slots added for {d['name']} on {d['date']}"})

@app.route("/slots/<professional>/<date>")
def view_slots(professional,date):
    rows = query_db("SELECT slot,capacity,booked_count,status FROM professional_slots WHERE professional=? AND date=?",(professional,date))
    return jsonify([{"slot":r[0],"capacity":r[1],"booked":r[2],"status":r[3]} for r in rows])

# --- Booking ---
@app.route("/book", methods=["POST"])
def book():
    d = request.json
    cust = query_db("SELECT name FROM users WHERE phone=? AND role='customer'",(d["phone"],),one=True)
    if not cust: return jsonify({"error":"Customer not registered"}),400
    cname = cust[0]
    query_db("INSERT INTO appointments (customer_phone,customer_name,professional,slot,date,status) VALUES (?,?,?,?,?,?)",
             (d["phone"],cname,d["professional"],d["slot"],d["date"],"Pending"))
    slot = query_db("SELECT capacity,booked_count FROM professional_slots WHERE professional=? AND date=? AND slot=?",
                    (d["professional"],d["date"],d["slot"]),one=True)
    if slot:
        cap,booked = slot; booked+=1
        status="Available"
        if booked>=cap: status="Full"
        elif booked>=cap*0.7: status="Filling Fast"
        query_db("UPDATE professional_slots SET booked_count=?,status=? WHERE professional=? AND date=? AND slot=?",
                 (booked,status,d["professional"],d["date"],d["slot"]))
    return jsonify({"message":"Appointment requested","customer_name":cname})

# --- Appointments & Metrics ---
@app.route("/appointments/professional/<name>/<date>")
def appts(name,date):
    rows=query_db("SELECT customer_name,customer_phone,slot,status FROM appointments WHERE professional=? AND date=?",(name,date))
    return jsonify([{"customer_name":r[0],"customer_phone":r[1],"slot":r[2],"status":r[3]} for r in rows])

@app.route("/metrics/<professional>/<date>")
def metrics(professional,date):
    rows=query_db("SELECT status FROM appointments WHERE professional=? AND date=?",(professional,date))
    m={"Pending":0,"Approved":0,"Rejected":0,"Cancelled":0}
    for r in rows:
        if r[0] in m: m[r[0]]+=1
    slots=query_db("SELECT capacity,booked_count FROM professional_slots WHERE professional=? AND date=?",(professional,date))
    m["Available Slots"]=sum(cap-booked for cap,booked in slots)
    return jsonify(m)

# --- Approve/Reject/Cancel ---
@app.route("/approve",methods=["POST"])
def approve():
    d=request.json
    query_db("UPDATE appointments SET status='Approved' WHERE customer_phone=? AND professional=? AND slot=? AND date=?",
             (d["phone"],d["professional"],d["slot"],d["date"]))
    return jsonify({"message":"Approved"})

@app.route("/reject",methods=["POST"])
def reject():
    d=request.json
    query_db("UPDATE appointments SET status='Rejected' WHERE customer_phone=? AND professional=? AND slot=? AND date=?",
             (d["phone"],d["professional"],d["slot"],d["date"]))
    return jsonify({"message":"Rejected"})

@app.route("/cancel",methods=["POST"])
def cancel():
    d=request.json
    query_db("UPDATE appointments SET status='Cancelled' WHERE customer_phone=? AND professional=? AND slot=? AND date=?",
             (d["phone"],d["professional"],d["slot"],d["date"]))
    # free seat
    slot=query_db("SELECT capacity,booked_count FROM professional_slots WHERE professional=? AND date=? AND slot=?",
                  (d["professional"],d["date"],d["slot"]),one=True)
    if slot:
        cap,booked=slot; booked=max(0,booked-1)
        status="Available"
        if booked>=cap: status="Full"
        elif booked>=cap*0.7: status="Filling Fast"
        query_db("UPDATE professional_slots SET booked_count=?,status=? WHERE professional=? AND date=? AND slot=?",
                 (booked,status,d["professional"],d["date"],d["slot"]))
    return jsonify({"message":"Cancelled"})

if __name__=="__main__": app.run(debug=True)
