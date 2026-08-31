from flask import Flask, request, jsonify, send_from_directory, session
import os
import datetime
import psycopg2
from psycopg2.extras import RealDictCursor


BASE = os.path.dirname(os.path.abspath(__file__))

DATABASE_URL = os.environ.get("DATABASE_URL")


app = Flask(__name__, static_folder="static")

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-in-production"
)


# ==================================================
# DATABASE CONNECTION
# ==================================================

class DBConnection:

    def __init__(self):
        self.connection = psycopg2.connect(
            DATABASE_URL,
            cursor_factory=RealDictCursor
        )

    def execute(self, sql, params=None):
        # पुराने SQLite ? placeholders को PostgreSQL %s में बदलें
        sql = sql.replace("?", "%s")

        cursor = self.connection.cursor()
        cursor.execute(sql, params or ())
        return cursor

    def executescript(self, sql):
        # PostgreSQL में एक-एक statement चलाएँ
        statements = [
            x.strip()
            for x in sql.split(";")
            if x.strip()
        ]

        for statement in statements:
            self.connection.cursor().execute(statement)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def conn():
    return DBConnection()


# ==================================================
# DATABASE INITIALIZATION
# ==================================================

def init_db():

    c = conn()

    c.executescript("""
CREATE TABLE IF NOT EXISTS families(
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    mobile TEXT DEFAULT '',
    pin TEXT DEFAULT '1234',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS savings(
    id SERIAL PRIMARY KEY,
    family_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    amount REAL NOT NULL,
    date TEXT NOT NULL,
    FOREIGN KEY(family_id) REFERENCES families(id)
);

CREATE TABLE IF NOT EXISTS loans(
    id SERIAL PRIMARY KEY,
    family_id INTEGER NOT NULL,
    original REAL NOT NULL,
    principal REAL NOT NULL,
    rate REAL DEFAULT 2,
    months INTEGER DEFAULT 12,
    date TEXT NOT NULL,
    FOREIGN KEY(family_id) REFERENCES families(id)
);

CREATE TABLE IF NOT EXISTS payments(
    id SERIAL PRIMARY KEY,
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
    id SERIAL PRIMARY KEY,
    total_interest REAL NOT NULL,
    date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications(
    id SERIAL PRIMARY KEY,
    family_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(family_id) REFERENCES families(id)
);
""")

    n = c.execute(
        "SELECT COUNT(*) AS n FROM families"
    ).fetchone()["n"]

    if n == 0:

        today = datetime.date.today().isoformat()

        for i in range(1, 24):

            c.execute(
                """
                INSERT INTO families
                (name, mobile, pin, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    f"परिवार {i}",
                    "",
                    "1234",
                    today
                )
            )

    c.commit()
    c.close()
# ==================================================
# HOME PAGE
# ==================================================

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ==================================================
# LOGIN
# ==================================================

@app.post("/api/login")
def login():

    data = request.json or {}

    login_type = data.get("type", "admin")
    pin = str(data.get("pin", ""))

    # -----------------------------
    # ADMIN LOGIN
    # -----------------------------

    if login_type == "admin":

        admin_pin = os.environ.get(
            "ADMIN_PIN",
            "1234"
        )

        if pin != admin_pin:
            return jsonify(
                error="गलत Admin PIN"
            ), 401

        session.clear()

        session["admin"] = True
        session["family_id"] = None

        return jsonify(
            ok=True,
            role="admin"
        )

    # -----------------------------
    # MEMBER LOGIN
    # -----------------------------

    if login_type == "member":

        try:
            family_id = int(
                data.get("family_id")
            )
        except (TypeError, ValueError):
            return jsonify(
                error="Member चुनें"
            ), 400

        c = conn()

        family = c.execute(
            """
            SELECT *
            FROM families
            WHERE id=? AND pin=?
            """,
            (
                family_id,
                pin
            )
        ).fetchone()

        c.close()

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

    return jsonify(
        error="Invalid login type"
    ), 400


# ==================================================
# LOGOUT
# ==================================================

@app.post("/api/logout")
def logout():

    session.clear()

    return jsonify(
        ok=True
    )


# ==================================================
# CURRENT USER
# ==================================================

@app.get("/api/me")
def me():

    return jsonify(
        admin=bool(
            session.get("admin")
        ),
        family_id=session.get(
            "family_id"
        )
    )


# ==================================================
# SECURITY
# ==================================================

@app.before_request
def protect_api():

    # ये API बिना login के भी चलेंगी
    public = {
        "/api/login",
        "/api/me",
        "/api/member-list"
    }

    # Public API को security check से बाहर रखें
    if request.path in public:
        return None

    # बाकी सभी API के लिए login जरूरी है
    if request.path.startswith("/api/"):

        if not session.get("admin") and not session.get("family_id"):
            return jsonify(
                error="Login required"
            ), 401

    return None

# ==================================================
# ADMIN REQUIRED
# ==================================================

def admin_required():

    if not session.get("admin"):

        return jsonify(
            error="Admin access required"
        ), 403

    return None


# ==================================================
# DASHBOARD
# ==================================================

@app.get("/api/dashboard")
def dashboard():

    error = admin_required()

    if error:
        return error

    c = conn()

    families = c.execute(
        """
        SELECT *
        FROM families
        ORDER BY id
        """
    ).fetchall()

    savings = c.execute(
        """
        SELECT COALESCE(SUM(amount), 0) x
        FROM savings
        """
    ).fetchone()["x"]

    loans = c.execute(
        """
        SELECT COALESCE(SUM(principal), 0) x
        FROM loans
        """
    ).fetchone()["x"]

    interest = c.execute(
        """
        SELECT COALESCE(SUM(interest), 0) x
        FROM payments
        """
    ).fetchone()["x"]

    c.close()

    return jsonify({
        "families": len(families),
        "savings": savings,
        "loans": loans,
        "interest": interest,
        "available": savings + interest - loans,
        "family_data": [
            dict(x)
            for x in families
        ]
    })


# ==================================================
# FAMILIES - GET
# ==================================================

@app.get("/api/families")
def get_families():

    error = admin_required()

    if error:
        return error

    c = conn()

    fs = c.execute(
        """
        SELECT *
        FROM families
        ORDER BY id
        """
    ).fetchall()

    out = []

    for f in fs:

        s = c.execute(
            """
            SELECT COALESCE(SUM(amount), 0) x
            FROM savings
            WHERE family_id=?
            """,
            (f["id"],)
        ).fetchone()["x"]

        l = c.execute(
            """
            SELECT COALESCE(SUM(principal), 0) x
            FROM loans
            WHERE family_id=?
            """,
            (f["id"],)
        ).fetchone()["x"]

        out.append({
            **dict(f),
            "savings": s,
            "loan": l
        })

    c.close()

    return jsonify(out)


# ==================================================
# ADD FAMILY
# ==================================================

@app.post("/api/families")
def add_family():

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    name = (
        d.get("name") or ""
    ).strip()

    mobile = (
        d.get("mobile") or ""
    ).strip()

    pin = str(
        d.get("pin") or "1234"
    ).strip()

    if not name:
        return jsonify(
            error="नाम जरूरी है"
        ), 400

    if len(pin) < 4:
        return jsonify(
            error="PIN कम से कम 4 अंक का होना चाहिए"
        ), 400

    c = conn()

    cur = c.execute(
        """
        INSERT INTO families
        (name, mobile, pin, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            name,
            mobile,
            pin,
            datetime.date.today().isoformat()
        )
    )

    c.commit()

    family_id = cur.lastrowid

    c.close()

    return jsonify(
        ok=True,
        id=family_id
    )


# ==================================================
# UPDATE FAMILY
# ==================================================

@app.put("/api/families/<int:fid>")
def update_family(fid):

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    name = (
        d.get("name") or ""
    ).strip()

    mobile = (
        d.get("mobile") or ""
    ).strip()

    pin = str(
        d.get("pin") or ""
    ).strip()

    if not name:
        return jsonify(
            error="नाम जरूरी है"
        ), 400

    if not pin:
        return jsonify(
            error="PIN जरूरी है"
        ), 400

    if len(pin) < 4:
        return jsonify(
            error="PIN कम से कम 4 अंक का होना चाहिए"
        ), 400

    c = conn()

    family = c.execute(
        """
        SELECT id
        FROM families
        WHERE id=?
        """,
        (fid,)
    ).fetchone()

    if not family:

        c.close()

        return jsonify(
            error="परिवार नहीं मिला"
        ), 404

    c.execute(
        """
        UPDATE families
        SET name=?, mobile=?, pin=?
        WHERE id=?
        """,
        (
            name,
            mobile,
            pin,
            fid
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# DELETE FAMILY
# ==================================================

@app.delete("/api/families/<int:fid>")
def delete_family(fid):

    error = admin_required()

    if error:
        return error

    c = conn()

    family = c.execute(
        """
        SELECT id
        FROM families
        WHERE id=?
        """,
        (fid,)
    ).fetchone()

    if not family:

        c.close()

        return jsonify(
            error="परिवार नहीं मिला"
        ), 404

    c.execute(
        "DELETE FROM payments WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM savings WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM loans WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM families WHERE id=?",
        (fid,)
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# MEMBER LIST FOR LOGIN
# ==================================================

@app.get("/api/member-list")
def member_list():

    c = conn()

    rows = c.execute(
        """
        SELECT id, name
        FROM families
        ORDER BY id
        """
    ).fetchall()

    c.close()

    return jsonify([
        {
            "id": r["id"],
            "name": r["name"]
        }
        for r in rows
    ])

# ==================================================
# MEMBER NOTIFICATIONS
# ==================================================

@app.get("/api/notifications")
def get_notifications():

    # सिर्फ Member अपनी notifications देख सकता है
    if session.get("admin"):
        return jsonify(
            error="Member access required"
        ), 403

    family_id = session.get("family_id")

    if not family_id:
        return jsonify(
            error="Login required"
        ), 401

    c = conn()

    rows = c.execute(
        """
        SELECT *
        FROM notifications
        WHERE family_id=?
        ORDER BY id DESC
        """,
        (family_id,)
    ).fetchall()

    c.close()

    return jsonify([
        dict(x)
        for x in rows
    ])
# ==================================================
# MEMBER PASSBOOK
# ==================================================

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

    group_savings = c.execute(
        "SELECT COALESCE(SUM(amount),0) x FROM savings"
    ).fetchone()["x"]

    group_loan = c.execute(
        "SELECT COALESCE(SUM(principal),0) x FROM loans"
    ).fetchone()["x"]

    group_interest = c.execute(
        "SELECT COALESCE(SUM(interest),0) x FROM payments"
    ).fetchone()["x"]

    group_available = (
        group_savings
        + group_interest
        - group_loan
    )

    f = c.execute(
        """
        SELECT *
        FROM families
        WHERE id=?
        """,
        (fid,)
    ).fetchone()

    if not f:
        c.close()

        return jsonify(
            error="परिवार नहीं मिला"
        ), 404

    s = c.execute(
        """
        SELECT *
        FROM savings
        WHERE family_id=?
        ORDER BY id DESC
        """,
        (fid,)
    ).fetchall()

    p = c.execute(
        """
        SELECT *
        FROM payments
        WHERE family_id=?
        ORDER BY id DESC
        """,
        (fid,)
    ).fetchall()

    l = c.execute(
        """
        SELECT *
        FROM loans
        WHERE family_id=?
        ORDER BY id DESC
        """,
        (fid,)
    ).fetchall()

    c.close()

    return jsonify({
        "family": dict(f),

        "group_savings": group_savings,
        "group_loan": group_loan,
        "group_interest": group_interest,
        "group_available": group_available,

        "savings": [dict(x) for x in s],
        "payments": [dict(x) for x in p],
        "loans": [dict(x) for x in l]
    })
# ==================================================
# SAVINGS - GET
# ==================================================

@app.get("/api/savings")
def savings():

    error = admin_required()

    if error:
        return error

    c = conn()

    rows = c.execute(
        """
        SELECT s.*, f.name family
        FROM savings s
        JOIN families f
        ON f.id=s.family_id
        ORDER BY s.id DESC
        """
    ).fetchall()

    c.close()

    return jsonify([
        dict(x)
        for x in rows
    ])


# ==================================================
# ADD SAVING
# ==================================================

@app.post("/api/savings")
def add_saving():

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    try:
        family_id = int(
            d.get("family_id")
        )

        amount = float(
            d.get("amount", 0)
        )

    except (TypeError, ValueError):

        return jsonify(
            error="बचत जानकारी सही दें"
        ), 400

    month = (
        d.get("month") or ""
    ).strip()

    if amount <= 0:

        return jsonify(
            error="बचत राशि सही दें"
        ), 400

    if not month:

        return jsonify(
            error="महीना जरूरी है"
        ), 400

    c = conn()

    family = c.execute(
        """
        SELECT id
        FROM families
        WHERE id=?
        """,
        (family_id,)
    ).fetchone()

    if not family:

        c.close()

        return jsonify(
            error="परिवार नहीं मिला"
        ), 404

    c.execute(
        """
        INSERT INTO savings
        (family_id, month, amount, date)
        VALUES (?, ?, ?, ?)
        """,
        (
            family_id,
            month,
            amount,
            d.get(
                "date",
                datetime.date.today().isoformat()
            )
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# UPDATE SAVING
# ==================================================

@app.put("/api/savings/<int:sid>")
def update_saving(sid):

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    try:

        family_id = int(
            d.get("family_id")
        )

        amount = float(
            d.get("amount", 0)
        )

    except (TypeError, ValueError):

        return jsonify(
            error="बचत जानकारी सही दें"
        ), 400

    month = (
        d.get("month") or ""
    ).strip()

    date = (
        d.get("date") or ""
    ).strip()

    if amount <= 0:

        return jsonify(
            error="बचत राशि सही दें"
        ), 400

    if not month or not date:

        return jsonify(
            error="महीना और तारीख जरूरी है"
        ), 400

    c = conn()

    row = c.execute(
        """
        SELECT id
        FROM savings
        WHERE id=?
        """,
        (sid,)
    ).fetchone()

    if not row:

        c.close()

        return jsonify(
            error="बचत एंट्री नहीं मिली"
        ), 404

    c.execute(
        """
        UPDATE savings
        SET family_id=?,
            month=?,
            amount=?,
            date=?
        WHERE id=?
        """,
        (
            family_id,
            month,
            amount,
            date,
            sid
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# DELETE SAVING
# ==================================================

@app.delete("/api/savings/<int:sid>")
def delete_saving(sid):

    error = admin_required()

    if error:
        return error

    c = conn()

    row = c.execute(
        """
        SELECT id
        FROM savings
        WHERE id=?
        """,
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

    return jsonify(
        ok=True
    )


# ==================================================
# LOANS - GET
# ==================================================

@app.get("/api/loans")
def loans():

    error = admin_required()

    if error:
        return error

    c = conn()

    rows = c.execute(
        """
        SELECT l.*, f.name family
        FROM loans l
        JOIN families f
        ON f.id=l.family_id
        ORDER BY l.id DESC
        """
    ).fetchall()

    c.close()

    return jsonify([
        dict(x)
        for x in rows
    ])


# ==================================================
# ADD LOAN
# ==================================================

@app.post("/api/loans")
def add_loan():

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    try:

        family_id = int(
            d.get("family_id")
        )

        amount = float(
            d.get("amount", 0)
        )

        rate = float(
            d.get("rate", 2)
        )

        months = int(
            d.get("months", 12)
        )

    except (TypeError, ValueError):

        return jsonify(
            error="लोन जानकारी सही दें"
        ), 400

    if amount <= 0:

        return jsonify(
            error="लोन राशि सही दें"
        ), 400

    if months <= 0:

        return jsonify(
            error="अवधि सही दें"
        ), 400

    c = conn()

    family = c.execute(
        """
        SELECT id
        FROM families
        WHERE id=?
        """,
        (family_id,)
    ).fetchone()

    if not family:

        c.close()

        return jsonify(
            error="परिवार नहीं मिला"
        ), 404

    c.execute(
        """
        INSERT INTO loans
        (family_id, original, principal, rate, months, date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            family_id,
            amount,
            amount,
            rate,
            months,
            d.get(
                "date",
                datetime.date.today().isoformat()
            )
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# UPDATE LOAN
# ==================================================

@app.put("/api/loans/<int:lid>")
def update_loan(lid):

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    try:

        family_id = int(
            d.get("family_id")
        )

        amount = float(
            d.get("amount", 0)
        )

        rate = float(
            d.get("rate", 2)
        )

        months = int(
            d.get("months", 12)
        )

    except (TypeError, ValueError):

        return jsonify(
            error="लोन जानकारी सही दें"
        ), 400

    if amount <= 0:

        return jsonify(
            error="लोन राशि सही दें"
        ), 400

    if months <= 0:

        return jsonify(
            error="अवधि सही दें"
        ), 400

    c = conn()

    loan = c.execute(
        """
        SELECT *
        FROM loans
        WHERE id=?
        """,
        (lid,)
    ).fetchone()

    if not loan:

        c.close()

        return jsonify(
            error="लोन नहीं मिला"
        ), 404

    paid_principal = (
        loan["original"]
        - loan["principal"]
    )

    if amount < paid_principal:

        c.close()

        return jsonify(
            error="नई लोन राशि अब तक चुकाए गए मूलधन से कम नहीं हो सकती"
        ), 400

    new_principal = (
        amount - paid_principal
    )

    c.execute(
        """
        UPDATE loans
        SET family_id=?,
            original=?,
            principal=?,
            rate=?,
            months=?
        WHERE id=?
        """,
        (
            family_id,
            amount,
            new_principal,
            rate,
            months,
            lid
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# DELETE LOAN
# ==================================================

@app.delete("/api/loans/<int:lid>")
def delete_loan(lid):

    error = admin_required()

    if error:
        return error

    c = conn()

    loan = c.execute(
        """
        SELECT id
        FROM loans
        WHERE id=?
        """,
        (lid,)
    ).fetchone()

    if not loan:

        c.close()

        return jsonify(
            error="लोन नहीं मिला"
        ), 404

    c.execute(
        "DELETE FROM payments WHERE loan_id=?",
        (lid,)
    )

    c.execute(
        "DELETE FROM loans WHERE id=?",
        (lid,)
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# PAYMENTS - GET
# ==================================================

@app.get("/api/payments")
def payments():

    error = admin_required()

    if error:
        return error

    c = conn()

    rows = c.execute(
        """
        SELECT p.*, f.name family
        FROM payments p
        JOIN families f
        ON f.id=p.family_id
        ORDER BY p.id DESC
        """
    ).fetchall()

    c.close()

    return jsonify([
        dict(x)
        for x in rows
    ])


# ==================================================
# ADD PAYMENT
# ==================================================

@app.post("/api/payments")
def payment():

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    try:

        amount = float(
            d.get("amount", 0)
        )

        lid = int(
            d.get("loan_id")
        )

    except (TypeError, ValueError):

        return jsonify(
            error="भुगतान जानकारी सही दें"
        ), 400

    if amount <= 0:

        return jsonify(
            error="भुगतान राशि सही दें"
        ), 400

    c = conn()

    l = c.execute(
        """
        SELECT *
        FROM loans
        WHERE id=?
        """,
        (lid,)
    ).fetchone()

    if not l:

        c.close()

        return jsonify(
            error="लोन नहीं मिला"
        ), 404

    if l["principal"] <= 0:

        c.close()

        return jsonify(
            error="इस लोन की पूरी राशि चुकाई जा चुकी है"
        ), 400

    # 2% interest calculation
    interest = min(
        l["principal"] * l["rate"] / 100,
        amount
    )

    principal = amount - interest

    principal = min(
        principal,
        l["principal"]
    )

    # अगर payment principal से ज्यादा हो
    actual_amount = (
        interest + principal
    )

    new_balance = (
        l["principal"] - principal
    )

    c.execute(
        """
        UPDATE loans
        SET principal=?
        WHERE id=?
        """,
        (
            new_balance,
            lid
        )
    )

    c.execute(
        """
        INSERT INTO payments
        (loan_id, family_id, amount, interest, principal, date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            lid,
            l["family_id"],
            actual_amount,
            interest,
            principal,
            d.get(
                "date",
                datetime.date.today().isoformat()
            )
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True,
        interest=interest,
        principal=principal,
        remaining=new_balance
    )


# ==================================================
# INTEREST DISTRIBUTION
# ==================================================

@app.post("/api/interest-distribution")
def distribution():

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    try:

        total = float(
            d.get("total_interest", 0)
        )

    except (TypeError, ValueError):

        return jsonify(
            error="ब्याज राशि सही दें"
        ), 400

    if total <= 0:

        return jsonify(
            error="ब्याज राशि सही दें"
        ), 400

    c = conn()

    total_s = c.execute(
        """
        SELECT COALESCE(SUM(amount), 0) x
        FROM savings
        """
    ).fetchone()["x"]

    if total_s <= 0:

        c.close()

        return jsonify(
            error="पहले बचत एंट्री करें"
        ), 400

    rows = c.execute(
        """
        SELECT
            f.id,
            f.name,
            COALESCE(SUM(s.amount), 0) savings
        FROM families f
        LEFT JOIN savings s
        ON s.family_id=f.id
        GROUP BY f.id
        ORDER BY f.id
        """
    ).fetchall()

    result = []

    for r in rows:

        share = (
            r["savings"]
            / total_s
        )

        result.append({
            **dict(r),
            "share": share,
            "interest": total * share
        })

    c.execute(
        """
        INSERT INTO interest_distributions
        (total_interest, date)
        VALUES (?, ?)
        """,
        (
            total,
            d.get(
                "date",
                datetime.date.today().isoformat()
            )
        )
    )

    c.commit()
    c.close()

    return jsonify(
        total_savings=total_s,
        result=result
    )


# ==================================================
# START APPLICATION
# ==================================================

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
