from flask import Flask, request, jsonify, send_from_directory, session
import sqlite3, os, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "family_saving.db")

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-in-production")


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = conn()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS families(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        mobile TEXT DEFAULT '',
        pin TEXT DEFAULT '1234',
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

    n = c.execute(
        "SELECT COUNT(*) n FROM families"
    ).fetchone()["n"]

    if n == 0:
        today = datetime.date.today().isoformat()

        for i in range(1, 24):
            c.execute("""
                INSERT INTO families(name, mobile, pin, created_at)
                VALUES(?,?,?,?)
            """, (
                f"परिवार {i}",
                "",
                "1234",
                today
            ))

    c.commit()
    c.close()


# --------------------------------------------------
# PAGE
# --------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# --------------------------------------------------
# LOGIN
# --------------------------------------------------

@app.post("/api/login")
def login():

    data = request.json or {}
    login_type = data.get("type", "admin")
    pin = str(data.get("pin", ""))

    # ADMIN LOGIN
    if login_type == "admin":

        admin_pin = os.environ.get(
            "ADMIN_PIN",
            "1234"
        )

        if pin != admin_pin:
            return jsonify(error="गलत Admin PIN"), 401

        session.clear()
        session["admin"] = True
        session["family_id"] = None

        return jsonify(
            ok=True,
            role="admin"
        )

    # MEMBER LOGIN
    if login_type == "member":

        try:
            family_id = int(data.get("family_id"))
        except:
            return jsonify(error="Member चुनें"), 400

        family = conn().execute(
            "SELECT * FROM families WHERE id=? AND pin=?",
            (family_id, pin)
        ).fetchone()

        if not family:
            return jsonify(
                error="गलत Member या PIN"
            ), 401

        session.clear()
        session["admin"] = False
        session["family_id"] = family_id

        return jsonify(
            ok=True,
            role="member",
            family_id=family_id,
            name=family["name"]
        )

    return jsonify(error="Invalid login type"), 400


@app.post("/api/logout")
def logout():

    session.clear()

    return jsonify(ok=True)


@app.get("/api/me")
def me():

    return jsonify(
        admin=bool(session.get("admin")),
        family_id=session.get("family_id")
    )


# --------------------------------------------------
# SECURITY
# --------------------------------------------------

@app.before_request
def protect_api():

    public = (
        "/api/login",
        "/api/me",
        "/api/member-list"
    )

    if (
        request.path.startswith("/api/")
        and request.path not in public
    ):

        if not session.get("admin") and not session.get("family_id"):
            return jsonify(
                error="Login required"
            ), 401

# --------------------------------------------------
# ADMIN ONLY
# --------------------------------------------------

def admin_required():

    if not session.get("admin"):
        return jsonify(
            error="Admin access required"
        ), 403

    return None


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.get("/api/dashboard")
def dashboard():

    error = admin_required()

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

    return jsonify({
        "families": len(families),
        "savings": savings,
        "loans": loans,
        "interest": interest,
        "available": savings + interest - loans,
        "family_data": [
            dict(x) for x in families
        ]
    })


# --------------------------------------------------
# FAMILIES
# --------------------------------------------------
# --------------------------------------------------
# MEMBER MANAGEMENT
# --------------------------------------------------

@app.put("/api/families/<int:fid>")
def update_family(fid):

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    name = (d.get("name") or "").strip()
    mobile = (d.get("mobile") or "").strip()
    pin = str(d.get("pin") or "").strip()

    if not name:
        return jsonify(error="नाम जरूरी है"), 400

    if not pin:
        return jsonify(error="PIN जरूरी है"), 400

    if len(pin) < 4:
        return jsonify(error="PIN कम से कम 4 अंक का होना चाहिए"), 400

    c = conn()

    family = c.execute(
        "SELECT id FROM families WHERE id=?",
        (fid,)
    ).fetchone()

    if not family:
        c.close()
        return jsonify(error="परिवार नहीं मिला"), 404

    c.execute("""
        UPDATE families
        SET name=?, mobile=?, pin=?
        WHERE id=?
    """, (
        name,
        mobile,
        pin,
        fid
    ))

    c.commit()
    c.close()

    return jsonify(ok=True)
@app.get("/api/families")
def get_families():

    error = admin_required()

    if error:
        return error

    c = conn()

    fs = c.execute(
        "SELECT * FROM families ORDER BY id"
    ).fetchall()

    out = []

    for f in fs:

        s = c.execute("""
            SELECT COALESCE(SUM(amount),0) x
            FROM savings
            WHERE family_id=?
        """, (f["id"],)).fetchone()["x"]

        l = c.execute("""
            SELECT COALESCE(SUM(principal),0) x
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

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    name = (d.get("name") or "").strip()

    if not name:
        return jsonify(
            error="नाम जरूरी है"
        ), 400

    c = conn()

    cur = c.execute("""
        INSERT INTO families
        (name,mobile,pin,created_at)
        VALUES(?,?,?,?)
    """, (
        name,
        d.get("mobile", ""),
        d.get("pin", "1234"),
        datetime.date.today().isoformat()
    ))

    c.commit()
    c.close()

    return jsonify(
        id=cur.lastrowid
    )

# --------------------------------------------------
# MEMBER LIST FOR LOGIN
# --------------------------------------------------

@app.get("/api/member-list")
def member_list():

    c = conn()

    rows = c.execute("""
        SELECT id, name
        FROM families
        ORDER BY id
    """).fetchall()

    c.close()

    return jsonify([
        {
            "id": r["id"],
            "name": r["name"]
        }
        for r in rows
    ])
# --------------------------------------------------
# MEMBER PASSBOOK
# --------------------------------------------------

@app.get("/api/family/<int:fid>/passbook")
def passbook(fid):

    # ADMIN can see anyone
    if session.get("admin"):

        allowed = True

    # MEMBER can ONLY see own data
    elif session.get("family_id") == fid:

        allowed = True

    else:

        return jsonify(
            error="आपको इस परिवार का data देखने की अनुमति नहीं है"
        ), 403

    c = conn()

    f = c.execute(
        "SELECT * FROM families WHERE id=?",
        (fid,)
    ).fetchone()

    if not f:
        c.close()

        return jsonify(
            error="परिवार नहीं मिला"
        ), 404

    s = c.execute("""
        SELECT *
        FROM savings
        WHERE family_id=?
        ORDER BY id DESC
    """, (fid,)).fetchall()

    p = c.execute("""
        SELECT *
        FROM payments
        WHERE family_id=?
        ORDER BY id DESC
    """, (fid,)).fetchall()

    l = c.execute("""
        SELECT *
        FROM loans
        WHERE family_id=?
        ORDER BY id DESC
    """, (fid,)).fetchall()

    c.close()

    return jsonify({
        "family": dict(f),
        "savings": [dict(x) for x in s],
        "payments": [dict(x) for x in p],
        "loans": [dict(x) for x in l]
    })


# --------------------------------------------------
# SAVINGS
# --------------------------------------------------

@app.get("/api/savings")
def savings():

    error = admin_required()

    if error:
        return error

    c = conn()

    rows = c.execute("""
        SELECT s.*, f.name family
        FROM savings s
        JOIN families f
        ON f.id=s.family_id
        ORDER BY s.id DESC
    """).fetchall()

    c.close()

    return jsonify(
        [dict(x) for x in rows]
    )


@app.post("/api/savings")
def add_saving():

    error = admin_required()

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


# --------------------------------------------------
# LOANS
# --------------------------------------------------

@app.get("/api/loans")
def loans():

    error = admin_required()

    if error:
        return error

    c = conn()

    rows = c.execute("""
        SELECT l.*, f.name family
        FROM loans l
        JOIN families f
        ON f.id=l.family_id
        ORDER BY l.id DESC
    """).fetchall()

    c.close()

    return jsonify(
        [dict(x) for x in rows]
    )


@app.post("/api/loans")
def add_loan():

    error = admin_required()

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


# --------------------------------------------------
# PAYMENTS
# --------------------------------------------------

@app.get("/api/payments")
def payments():

    error = admin_required()

    if error:
        return error

    c = conn()

    rows = c.execute("""
        SELECT p.*, f.name family
        FROM payments p
        JOIN families f
        ON f.id=p.family_id
        ORDER BY p.id DESC
    """).fetchall()

    c.close()

    return jsonify(
        [dict(x) for x in rows]
    )


@app.post("/api/payments")
def payment():

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    amount = float(
        d.get("amount", 0)
    )

    lid = int(
        d["loan_id"]
    )

    c = conn()

    l = c.execute(
        "SELECT * FROM loans WHERE id=?",
        (lid,)
    ).fetchone()

    if not l or amount <= 0:
        c.close()

        return jsonify(
            error="भुगतान जानकारी सही दें"
        ), 400

    interest = min(
        l["principal"] * 2 / 100,
        amount
    )

    principal = amount - interest

    principal = min(
        principal,
        l["principal"]
    )

    c.execute(
        "UPDATE loans SET principal=? WHERE id=?",
        (
            l["principal"] - principal,
            lid
        )
    )

    c.execute("""
        INSERT INTO payments
        (loan_id,family_id,amount,interest,principal,date)
        VALUES(?,?,?,?,?,?)
    """, (
        lid,
        l["family_id"],
        amount,
        interest,
        principal,
        d.get(
            "date",
            datetime.date.today().isoformat()
        )
    ))

    c.commit()
    c.close()

    return jsonify(
        ok=True,
        interest=interest,
        principal=principal
    )


# --------------------------------------------------
# INTEREST DISTRIBUTION
# --------------------------------------------------

@app.post("/api/interest-distribution")
def distribution():

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    total = float(
        d.get("total_interest", 0)
    )

    if total <= 0:
        return jsonify(
            error="ब्याज राशि सही दें"
        ), 400

    c = conn()

    total_s = c.execute(
        "SELECT COALESCE(SUM(amount),0)x FROM savings"
    ).fetchone()["x"]

    if total_s <= 0:
        c.close()

        return jsonify(
            error="पहले बचत एंट्री करें"
        ), 400

    rows = c.execute("""
        SELECT
            f.id,
            f.name,
            COALESCE(SUM(s.amount),0) savings
        FROM families f
        LEFT JOIN savings s
        ON s.family_id=f.id
        GROUP BY f.id
        ORDER BY f.id
    """).fetchall()

    result = []

    for r in rows:

        share = r["savings"] / total_s

        result.append({
            **dict(r),
            "share": share,
            "interest": total * share
        })

    c.execute("""
        INSERT INTO interest_distributions
        (total_interest,date)
        VALUES(?,?)
    """, (
        total,
        d.get(
            "date",
            datetime.date.today().isoformat()
        )
    ))

    c.commit()
    c.close()

    return jsonify(
        total_savings=total_s,
        result=result
    )


# --------------------------------------------------
# START
# --------------------------------------------------

init_db()

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                8000
            )
        ),
        debug=False
    )
