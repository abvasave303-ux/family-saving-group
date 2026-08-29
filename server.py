from flask import Flask, request, jsonify, send_from_directory, session
import sqlite3
import os
import hashlib
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "family_saving.db")

app = Flask(__name__, static_folder="static")

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-in-production"
)


# =========================================================
# DATABASE
# =========================================================

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def hash_password(password):
    return hashlib.sha256(
        str(password).encode("utf-8")
    ).hexdigest()


def init_db():

    c = conn()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS families(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      mobile TEXT DEFAULT '',
      pin TEXT DEFAULT '1234',
      username TEXT UNIQUE,
      password_hash TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS savings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      family_id INTEGER NOT NULL,
      month TEXT NOT NULL,
      amount REAL NOT NULL,
      date TEXT NOT NULL,
      FOREIGN KEY(family_id) REFERENCES families(id)
    );

    CREATE TABLE IF NOT EXISTS loans(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      family_id INTEGER NOT NULL,
      original REAL NOT NULL,
      principal REAL NOT NULL,
      rate REAL DEFAULT 2,
      months INTEGER DEFAULT 12,
      date TEXT NOT NULL,
      FOREIGN KEY(family_id) REFERENCES families(id)
    );

    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      loan_id INTEGER NOT NULL,
      family_id INTEGER NOT NULL,
      amount REAL NOT NULL,
      interest REAL NOT NULL,
      principal REAL NOT NULL,
      date TEXT NOT NULL,
      FOREIGN KEY(loan_id) REFERENCES loans(id),
      FOREIGN KEY(family_id) REFERENCES families(id)
    );

    CREATE TABLE IF NOT EXISTS interest_distributions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      total_interest REAL NOT NULL,
      date TEXT NOT NULL
    );
    """)

    # -----------------------------------------------------
    # Existing database compatibility
    # -----------------------------------------------------

    columns = [
        x["name"]
        for x in c.execute("PRAGMA table_info(families)").fetchall()
    ]

    if "username" not in columns:
        c.execute(
            "ALTER TABLE families ADD COLUMN username TEXT"
        )

    if "password_hash" not in columns:
        c.execute(
            "ALTER TABLE families ADD COLUMN password_hash TEXT"
        )

    # -----------------------------------------------------
    # Create 23 families if database is empty
    # -----------------------------------------------------

    n = c.execute(
        "SELECT COUNT(*) n FROM families"
    ).fetchone()["n"]

    today = datetime.date.today().isoformat()

    if n == 0:

        for i in range(1, 24):

            username = f"member{i}"

            password = "1234"

            c.execute("""
                INSERT INTO families
                (name, mobile, pin, username, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                f"परिवार {i}",
                "",
                "1234",
                username,
                hash_password(password),
                today
            ))

    else:

        # -------------------------------------------------
        # Add login credentials to old family records
        # -------------------------------------------------

        rows = c.execute(
            "SELECT id, username, password_hash FROM families ORDER BY id"
        ).fetchall()

        for i, row in enumerate(rows, start=1):

            username = row["username"]

            password_hash = row["password_hash"]

            if not username:
                username = f"member{i}"

            if not password_hash:
                password_hash = hash_password("1234")

            c.execute("""
                UPDATE families
                SET username=?, password_hash=?
                WHERE id=?
            """, (
                username,
                password_hash,
                row["id"]
            ))

    c.commit()
    c.close()


# =========================================================
# LOGIN / SESSION
# =========================================================

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.post("/api/login")
def login():

    data = request.json or {}

    login_type = str(
        data.get("type", "admin")
    ).lower()

    # -----------------------------------------------------
    # ADMIN LOGIN
    # -----------------------------------------------------

    if login_type == "admin":

        pin = os.environ.get(
            "ADMIN_PIN",
            "1234"
        )

        entered_pin = str(
            data.get("pin", "")
        )

        if entered_pin != pin:
            return jsonify(
                error="गलत Admin PIN"
            ), 401

        session.clear()

        session["admin"] = True
        session["role"] = "admin"

        return jsonify(
            ok=True,
            role="admin"
        )

    # -----------------------------------------------------
    # MEMBER LOGIN
    # -----------------------------------------------------

    username = str(
        data.get("username", "")
    ).strip()

    password = str(
        data.get("password", "")
    )

    if not username or not password:
        return jsonify(
            error="Username और Password जरूरी है"
        ), 400

    c = conn()

    member = c.execute("""
        SELECT *
        FROM families
        WHERE username=?
    """, (username,)).fetchone()

    c.close()

    if not member:
        return jsonify(
            error="Username या Password गलत है"
        ), 401

    if not member["password_hash"]:
        return jsonify(
            error="इस Member का Login अभी सेट नहीं है"
        ), 401

    if hash_password(password) != member["password_hash"]:
        return jsonify(
            error="Username या Password गलत है"
        ), 401

    session.clear()

    session["admin"] = False
    session["role"] = "member"
    session["family_id"] = member["id"]

    return jsonify(
        ok=True,
        role="member",
        family_id=member["id"],
        name=member["name"]
    )


# =========================================================
# LOGOUT
# =========================================================

@app.post("/api/logout")
def logout():

    session.clear()

    return jsonify(ok=True)


# =========================================================
# CURRENT USER
# =========================================================

@app.get("/api/me")
def me():

    if session.get("role") == "admin":

        return jsonify(
            logged_in=True,
            admin=True,
            role="admin"
        )

    if session.get("role") == "member":

        fid = session.get("family_id")

        c = conn()

        f = c.execute(
            "SELECT id,name,mobile,username FROM families WHERE id=?",
            (fid,)
        ).fetchone()

        c.close()

        if not f:
            session.clear()

            return jsonify(
                logged_in=False
            )

        return jsonify(
            logged_in=True,
            admin=False,
            role="member",
            family_id=f["id"],
            name=f["name"],
            mobile=f["mobile"],
            username=f["username"]
        )

    return jsonify(
        logged_in=False,
        admin=False
    )


# =========================================================
# SECURITY HELPERS
# =========================================================

def require_admin():

    if session.get("role") != "admin":
        return jsonify(
            error="Admin access required"
        ), 403

    return None


def get_member_family_id():

    if session.get("role") != "member":
        return None

    return session.get("family_id")


# =========================================================
# DASHBOARD
# =========================================================

@app.get("/api/dashboard")
def dashboard():

    # Member gets ONLY his own dashboard
    if session.get("role") == "member":

        fid = get_member_family_id()

        c = conn()

        f = c.execute(
            "SELECT * FROM families WHERE id=?",
            (fid,)
        ).fetchone()

        savings = c.execute("""
            SELECT COALESCE(SUM(amount),0) x
            FROM savings
            WHERE family_id=?
        """, (fid,)).fetchone()["x"]

        loans = c.execute("""
            SELECT COALESCE(SUM(principal),0) x
            FROM loans
            WHERE family_id=?
        """, (fid,)).fetchone()["x"]

        interest = c.execute("""
            SELECT COALESCE(SUM(interest),0) x
            FROM payments
            WHERE family_id=?
        """, (fid,)).fetchone()["x"]

        c.close()

        return jsonify({
            "families": 1,
            "savings": savings,
            "loans": loans,
            "interest": interest,
            "available": savings + interest - loans,
            "family_data": [dict(f)] if f else []
        })

    # Admin dashboard
    error = require_admin()

    if error:
        return error

    c = conn()

    families = c.execute(
        "SELECT * FROM families ORDER BY id"
    ).fetchall()

    savings = c.execute(
        "SELECT COALESCE(SUM(amount),0) x FROM savings"
    ).fetchone()["x"]

    loans = c.execute(
        "SELECT COALESCE(SUM(principal),0) x FROM loans"
    ).fetchone()["x"]

    interest = c.execute(
        "SELECT COALESCE(SUM(interest),0) x FROM payments"
    ).fetchone()["x"]

    c.close()

    return jsonify({
        "families": len(families),
        "savings": savings,
        "loans": loans,
        "interest": interest,
        "available": savings + interest - loans,
        "family_data": [dict(x) for x in families]
    })


# =========================================================
# FAMILIES
# =========================================================

@app.get("/api/families")
def get_families():

    error = require_admin()

    if error:
        return error

    c = conn()

    fs = c.execute(
        "SELECT * FROM families ORDER BY id"
    ).fetchall()

    out = []

    for f in fs:

        s = c.execute("""
            SELECT COALESCE(SUM(amount),0)x
            FROM savings
            WHERE family_id=?
        """, (f["id"],)).fetchone()["x"]

        l = c.execute("""
            SELECT COALESCE(SUM(principal),0)x
            FROM loans
            WHERE family_id=?
        """, (f["id"],)).fetchone()["x"]

        out.append({
            **dict(f),
            "savings": s,
            "loan": l
        })

    c.close()

    return jsonify(out)


@app.post("/api/families")
def add_family():

    error = require_admin()

    if error:
        return error

    d = request.json or {}

    name = (d.get("name") or "").strip()

    if not name:
        return jsonify(
            error="नाम जरूरी है"
        ), 400

    c = conn()

    # Find next member username
    count = c.execute(
        "SELECT COUNT(*) n FROM families"
    ).fetchone()["n"]

    username = f"member{count + 1}"

    password = str(
        d.get("password", "1234")
    )

    cur = c.execute("""
        INSERT INTO families
        (name,mobile,username,password_hash,created_at)
        VALUES(?,?,?,?,?)
    """, (
        name,
        d.get("mobile", ""),
        username,
        hash_password(password),
        datetime.date.today().isoformat()
    ))

    c.commit()
    c.close()

    return jsonify(
        id=cur.lastrowid,
        username=username
    )


# =========================================================
# UPDATE MEMBER
# =========================================================

@app.put("/api/families/<int:fid>")
def update_family(fid):

    error = require_admin()

    if error:
        return error

    d = request.json or {}

    c = conn()

    family = c.execute(
        "SELECT * FROM families WHERE id=?",
        (fid,)
    ).fetchone()

    if not family:

        c.close()

        return jsonify(
            error="परिवार नहीं मिला"
        ), 404

    name = (
        d.get("name", family["name"])
        or ""
    ).strip()

    mobile = d.get(
        "mobile",
        family["mobile"]
    )

    username = d.get(
        "username",
        family["username"]
    )

    if not name or not username:

        c.close()

        return jsonify(
            error="नाम और Username जरूरी है"
        ), 400

    # Check duplicate username
    duplicate = c.execute("""
        SELECT id
        FROM families
        WHERE username=? AND id!=?
    """, (username, fid)).fetchone()

    if duplicate:

        c.close()

        return jsonify(
            error="यह Username पहले से मौजूद है"
        ), 400

    c.execute("""
        UPDATE families
        SET name=?, mobile=?, username=?
        WHERE id=?
    """, (
        name,
        mobile,
        username,
        fid
    ))

    # Password change
    new_password = d.get("password")

    if new_password:

        c.execute("""
            UPDATE families
            SET password_hash=?
            WHERE id=?
        """, (
            hash_password(new_password),
            fid
        ))

    c.commit()
    c.close()

    return jsonify(ok=True)


# =========================================================
# DELETE FAMILY
# =========================================================

@app.delete("/api/families/<int:fid>")
def delete_family(fid):

    error = require_admin()

    if error:
        return error

    c = conn()

    family = c.execute(
        "SELECT * FROM families WHERE id=?",
        (fid,)
    ).fetchone()

    if not family:

        c.close()

        return jsonify(
            error="परिवार नहीं मिला"
        ), 404

    savings = c.execute(
        "SELECT COUNT(*) n FROM savings WHERE family_id=?",
        (fid,)
    ).fetchone()["n"]

    loans = c.execute(
        "SELECT COUNT(*) n FROM loans WHERE family_id=?",
        (fid,)
    ).fetchone()["n"]

    payments = c.execute(
        "SELECT COUNT(*) n FROM payments WHERE family_id=?",
        (fid,)
    ).fetchone()["n"]

    if savings or loans or payments:

        c.close()

        return jsonify(
            error="इस परिवार का वित्तीय रिकॉर्ड मौजूद है। पहले रिकॉर्ड हटाना होगा।"
        ), 400

    c.execute(
        "DELETE FROM families WHERE id=?",
        (fid,)
    )

    c.commit()
    c.close()

    return jsonify(ok=True)


# =========================================================
# SAVINGS
# =========================================================

@app.post("/api/savings")
def add_saving():

    error = require_admin()

    if error:
        return error

    d = request.json or {}

    amount = float(
        d.get("amount", 0)
    )

    if amount <= 0:

        return jsonify(
            error="बचत राशि सही दें"
        ), 400

    c = conn()

    c.execute("""
        INSERT INTO savings
        (family_id,month,amount,date)
        VALUES(?,?,?,?)
    """, (
        int(d["family_id"]),
        d["month"],
        amount,
        d.get(
            "date",
            datetime.date.today().isoformat()
        )
    ))

    c.commit()
    c.close()

    return jsonify(ok=True)


@app.put("/api/savings/<int:sid>")
def update_saving(sid):

    error = require_admin()

    if error:
        return error

    d = request.json or {}

    amount = float(
        d.get("amount", 0)
    )

    if amount <= 0:

        return jsonify(
            error="बचत राशि सही दें"
        ), 400

    c = conn()

    row = c.execute(
        "SELECT id FROM savings WHERE id=?",
        (sid,)
    ).fetchone()

    if not row:

        c.close()

        return jsonify(
            error="बचत एंट्री नहीं मिली"
        ), 404

    c.execute("""
        UPDATE savings
        SET family_id=?, month=?, amount=?, date=?
        WHERE id=?
    """, (
        int(d["family_id"]),
        d["month"],
        amount,
        d["date"],
        sid
    ))

    c.commit()
    c.close()

    return jsonify(ok=True)


@app.delete("/api/savings/<int:sid>")
def delete_saving(sid):

    error = require_admin()

    if error:
        return error

    c = conn()

    row = c.execute(
        "SELECT id FROM savings WHERE id=?",
        (sid,)
    ).fetchone()

    if not row:

        c.close()

        return jsonify(
            error="बचत एंट्री नहीं मिली"
        ), 404

    c.execute(
        "DELETE FROM savings WHERE id=?",
        (sid,)
    )

    c.commit()
    c.close()

    return jsonify(ok=True)


@app.get("/api/savings")
def savings():

    error = require_admin()

    if error:
        return error

    c = conn()

    rows = c.execute("""
        SELECT s.*,f.name family
        FROM savings s
        JOIN families f
        ON f.id=s.family_id
        ORDER BY s.id DESC
    """).fetchall()

    c.close()

    return jsonify(
        [dict(x) for x in rows]
    )


# =========================================================
# MEMBER OWN SAVINGS
# =========================================================

@app.get("/api/member/savings")
def member_savings():

    if session.get("role") != "member":

        return jsonify(
            error="Member access required"
        ), 403

    fid = get_member_family_id()

    c = conn()

    rows = c.execute("""
        SELECT *
        FROM savings
        WHERE family_id=?
        ORDER BY id DESC
    """, (fid,)).fetchall()

    c.close()

    return jsonify(
        [dict(x) for x in rows]
    )


# =========================================================
# LOANS
# =========================================================

@app.post("/api/loans")
def add_loan():

    error = require_admin()

    if error:
        return error

    d = request.json or {}

    amount = float(
        d.get("amount", 0)
    )

    if amount <= 0:

        return jsonify(
            error="लोन राशि सही दें"
        ), 400

    c = conn()

    c.execute("""
        INSERT INTO loans
        (family_id,original,principal,rate,months,date)
        VALUES(?,?,?,?,?,?)
    """, (
        int(d["family_id"]),
        amount,
        amount,
        float(d.get("rate", 2)),
        int(d.get("months", 12)),
        d.get(
            "date",
            datetime.date.today().isoformat()
        )
    ))

    c.commit()
    c.close()

    return jsonify(ok=True)


@app.put("/api/loans/<int:lid>")
def update_loan(lid):

    error = require_admin()

    if error:
        return error

    d = request.json or {}

    amount = float(
        d.get("amount", 0)
    )

    if amount <= 0:

        return jsonify(
            error="लोन राशि सही दें"
        ), 400

    c = conn()

    loan = c.execute(
        "SELECT * FROM loans WHERE id=?",
        (lid,)
    ).fetchone()

    if not loan:

        c.close()

        return jsonify(
            error="लोन नहीं मिला"
        ), 404

    paid = c.execute("""
        SELECT COUNT(*) n
        FROM payments
        WHERE loan_id=?
    """, (lid,)).fetchone()["n"]

    if paid > 0:

        c.close()

        return jsonify(
            error="इस लोन पर payment हो चुकी है, इसलिए इसे edit नहीं किया जा सकता"
        ), 400

    c.execute("""
        UPDATE loans
        SET family_id=?, original=?, principal=?,
            rate=?, months=?, date=?
        WHERE id=?
    """, (
        int(d["family_id"]),
        amount,
        amount,
        float(d.get("rate", 2)),
        int(d.get("months", 12)),
        d.get(
            "date",
            datetime.date.today().isoformat()
        ),
        lid
    ))

    c.commit()
    c.close()
