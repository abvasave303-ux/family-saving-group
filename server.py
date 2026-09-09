from flask import Flask, request, jsonify, send_from_directory, session
import os
import datetime
import secrets
import firebase_admin
from firebase_admin import credentials, messaging
import psycopg2
from psycopg2.extras import RealDictCursor


BASE = os.path.dirname(os.path.abspath(__file__))

DATABASE_URL = os.environ.get("DATABASE_URL")


app = Flask(__name__, static_folder="static")
@app.get("/firebase-messaging-sw.js")
def firebase_messaging_sw():
    return send_from_directory(
        BASE,
        "firebase-messaging-sw.js"
    )
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-in-production"
)
# FIREBASE ADMIN
firebase_cred = credentials.Certificate(
    "/etc/secrets/firebase-service-account.json"
)

if not firebase_admin._apps:
    firebase_admin.initialize_app(firebase_cred)

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

    CREATE TABLE IF NOT EXISTS saving_debits(
        id SERIAL PRIMARY KEY,
        family_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        reason TEXT DEFAULT '',
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
    CREATE TABLE IF NOT EXISTS interest_credits(
        id SERIAL PRIMARY KEY,
        family_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        distribution_id INTEGER,
        FOREIGN KEY(family_id) REFERENCES families(id),
        FOREIGN KEY(distribution_id) REFERENCES interest_distributions(id)
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

    CREATE TABLE IF NOT EXISTS fcm_tokens(
        id SERIAL PRIMARY KEY,
        family_id INTEGER NOT NULL UNIQUE,
        token TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(family_id) REFERENCES families(id)
    );
    
    CREATE TABLE IF NOT EXISTS active_member_sessions(
        family_id INTEGER PRIMARY KEY,
        session_token TEXT NOT NULL,
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

        c = conn()

        active_session = c.execute(
            """
            SELECT session_token
            FROM active_member_sessions
            WHERE family_id=?
            """,
            (family_id,)
        ).fetchone()

        c.close()

        if active_session:
            return jsonify(
                error="यह परिवार पहले से किसी दूसरे device पर login है।"
            ), 409

        session_token = secrets.token_urlsafe(32)

        c = conn()

        c.execute(
            """
            INSERT INTO active_member_sessions
            (family_id, session_token, created_at)
            VALUES (?, ?, ?)
            """,
            (
                family_id,
                session_token,
                datetime.datetime.now().isoformat()
            )
        )

        c.commit()
        c.close()

        session.clear()
        session["session_token"] = session_token
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
# MEMBER CHANGE PIN
# ==================================================

@app.post("/api/member/change-pin")
def change_member_pin():

    family_id = session.get("family_id")

    if not family_id:
        return jsonify(
            error="Member login required"
        ), 401

    data = request.json or {}

    current_pin = str(
        data.get("current_pin", "")
    )

    new_pin = str(
        data.get("new_pin", "")
    )

    confirm_pin = str(
        data.get("confirm_pin", "")
    )

    if not current_pin:
        return jsonify(
            error="Current PIN डालें"
        ), 400

    if not new_pin:
        return jsonify(
            error="New PIN डालें"
        ), 400

    if new_pin != confirm_pin:
        return jsonify(
            error="New PIN और Confirm PIN अलग हैं"
        ), 400

    if len(new_pin) < 4:
        return jsonify(
            error="PIN कम से कम 4 अंक का होना चाहिए"
        ), 400

    c = conn()

    family = c.execute(
        """
        SELECT pin
        FROM families
        WHERE id=?
        """,
        (family_id,)
    ).fetchone()

    if not family:
        c.close()
        return jsonify(
            error="Member नहीं मिला"
        ), 404

    if str(family["pin"]) != current_pin:
        c.close()
        return jsonify(
            error="Current PIN गलत है"
        ), 401

    c.execute(
        """
        UPDATE families
        SET pin=?
        WHERE id=?
        """,
        (
            new_pin,
            family_id
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True,
        message="PIN successfully changed"
    )
# ==================================================
# ADMIN FORCE LOGOUT MEMBER
# ==================================================

@app.post("/api/admin/force-logout/<int:fid>")
def admin_force_logout(fid):

    error = admin_required()

    if error:
        return error

    c = conn()

    family = c.execute(
        """
        SELECT id, name
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
        DELETE FROM active_member_sessions
        WHERE family_id=?
        """,
        (fid,)
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True,
        message=f"{family['name']} का active login सफलतापूर्वक Force Logout हो गया।"
    )
# ==================================================
# LOGOUT
# ==================================================

@app.post("/api/logout")
def logout():

    family_id = session.get("family_id")
    session_token = session.get("session_token")

    if family_id and session_token:

        c = conn()

        c.execute(
            """
            DELETE FROM active_member_sessions
            WHERE family_id=? AND session_token=?
            """,
            (
                family_id,
                session_token
            )
        )

        c.commit()
        c.close()

    session.clear()

    return jsonify(
        ok=True
    )


# ==================================================
# CURRENT USER
# ==================================================

@app.get("/api/me")
def me():

    family_id = session.get("family_id")

    name = None

    if family_id:

        c = conn()

        family = c.execute(
            """
            SELECT name
            FROM families
            WHERE id=?
            """,
            (family_id,)
        ).fetchone()

        c.close()

        if family:
            name = family["name"]

    return jsonify(
        admin=bool(
            session.get("admin")
        ),
        family_id=family_id,
        name=name
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

        # Admin session
        if session.get("admin"):
            return None

        # Member session
        if session.get("family_id"):

            if not member_session_valid():

                session.clear()

                return jsonify(
                    error="Session expired. Please login again."
                ), 401

            return None

        # कोई valid login नहीं
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

# MEMBER SESSION VALIDATION

# ==================================================

def member_session_valid():

    family_id = session.get("family_id")
    session_token = session.get("session_token")

    if not family_id or not session_token:
        return False

    c = conn()

    row = c.execute(
        """
        SELECT session_token
        FROM active_member_sessions
        WHERE family_id=?
        """,
        (family_id,)
    ).fetchone()

    c.close()

    return bool(
        row and
        row["session_token"] == session_token
    )


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
        SELECT
            (
                SELECT COALESCE(SUM(amount), 0)
                FROM savings
            )
            -
            (
                SELECT COALESCE(SUM(amount), 0)
                FROM saving_debits
            ) x
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
            SELECT
                (
                    SELECT COALESCE(SUM(amount), 0)
                    FROM savings
                    WHERE family_id=?
                )
                -
                (
                    SELECT COALESCE(SUM(amount), 0)
                    FROM saving_debits
                    WHERE family_id=?
                ) x
            """,
            (f["id"], f["id"])
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

    c = conn()

    family = c.execute(
        """
        SELECT id, pin
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

    # PIN blank ho to existing PIN ko preserve karo.
    if not pin:
        pin = str(family["pin"] or "").strip()

    if not pin:
        c.close()
        return jsonify(
            error="PIN जरूरी है"
        ), 400

    if len(pin) < 4:
        c.close()
        return jsonify(
            error="PIN कम से कम 4 अंक का होना चाहिए"
        ), 400

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

    # पहले जुड़े हुए records हटाएँ

    c.execute(
        "DELETE FROM payments WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM savings WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM saving_debits WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM loans WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM active_member_sessions WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM notifications WHERE family_id=?",
        (fid,)
    )

    c.execute(
        "DELETE FROM fcm_tokens WHERE family_id=?",
        (fid,)
    )

    # आखिर में family हटाएँ

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
@app.post("/api/notifications/read")
def mark_notifications_read():

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

    c.execute(
        """
        UPDATE notifications
        SET is_read=TRUE
        WHERE family_id=?
        """,
        (family_id,)
    )

    c.commit()
    c.close()

    return jsonify(ok=True)
@app.post("/api/fcm-token")
def save_fcm_token():

    if session.get("admin"):
        return jsonify(
            error="Member access required"
        ), 403

    family_id = session.get("family_id")

    if not family_id:
        return jsonify(
            error="Login required"
        ), 401

    data = request.get_json() or {}
    token = data.get("token")

    if not token:
        return jsonify(
            error="FCM token required"
        ), 400

    c = conn()

    now = datetime.datetime.now().isoformat()

    c.execute(
        """
        INSERT INTO fcm_tokens
        (family_id, token, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT (family_id)
        DO UPDATE SET
            token = EXCLUDED.token,
            updated_at = EXCLUDED.updated_at
        """,
        (family_id, token, now)
    )

    c.commit()
    c.close()

    return jsonify(ok=True)
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

    d = c.execute(
        """
        SELECT *
        FROM saving_debits
        WHERE family_id=?
        ORDER BY id DESC
        """,
        (fid,)
    ).fetchall()
# ==============================
# INTEREST CREDITS
# ==============================
    ic = c.execute(
        """
        SELECT *
        FROM interest_credits
        WHERE family_id=?
        ORDER BY id DESC
        """,
        (fid,)
    ).fetchall()
# ==============================
# FINANCIAL YEAR RECORDS
# APRIL TO MARCH
# ==============================

    yearly_records = {}
    
    for r in s:
        amount = float(r["amount"] or 0)
        dt = str(r["date"])
    
        year = int(dt[:4])
        month = int(dt[5:7])
    
        fy_start = year if month >= 4 else year - 1
        fy_name = f"{fy_start}-{str(fy_start + 1)[-2:]}"
    
        if fy_name not in yearly_records:
            yearly_records[fy_name] = {
                "financial_year": fy_name,
                "saving": 0,
                "interest": 0,
                "debit": 0
            }
    
        yearly_records[fy_name]["saving"] += amount
    
    
    for r in ic:
        amount = float(r["amount"] or 0)
        dt = str(r["date"])
    
        year = int(dt[:4])
        month = int(dt[5:7])
    
        fy_start = year if month >= 4 else year - 1
        fy_name = f"{fy_start}-{str(fy_start + 1)[-2:]}"
    
        if fy_name not in yearly_records:
            yearly_records[fy_name] = {
                "financial_year": fy_name,
                "saving": 0,
                "interest": 0,
                "debit": 0
            }
    
        yearly_records[fy_name]["interest"] += amount
    
    
    for r in d:
        amount = float(r["amount"] or 0)
        dt = str(r["date"])
    
        year = int(dt[:4])
        month = int(dt[5:7])
    
        fy_start = year if month >= 4 else year - 1
        fy_name = f"{fy_start}-{str(fy_start + 1)[-2:]}"
    
        if fy_name not in yearly_records:
            yearly_records[fy_name] = {
                "financial_year": fy_name,
                "saving": 0,
                "interest": 0,
                "debit": 0
            }
    
        yearly_records[fy_name]["debit"] += amount
    
    
    yearly_records = list(yearly_records.values())
    
    for r in yearly_records:
        r["net_saving"] = (
            r["saving"]
            + r["interest"]
        )
    c.close()

    return jsonify({
        "family": dict(f),

        "group_savings": group_savings,
        "group_loan": group_loan,
        "group_interest": group_interest,
        "group_available": group_available,

        "savings": [dict(x) for x in s],
        "payments": [dict(x) for x in p],
        "loans": [dict(x) for x in l],
        "saving_debits": [dict(x) for x in d],
        "interest_credits": [dict(x) for x in ic],
        "yearly_records": yearly_records
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
        SELECT id, name
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

    entry_date = d.get(
        "date",
        datetime.date.today().isoformat()
    )

    # ==============================
    # SAVE MONTHLY SAVING
    # ==============================

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
            entry_date
        )
    )

    # ==============================
    # CREATE MEMBER NOTIFICATION
    # ==============================

    c.execute(
        """
        INSERT INTO notifications
        (family_id, title, message, is_read, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            family_id,
            "💰 बचत अपडेट",
            f"आपकी {month} महीने की ₹{amount:.2f} बचत अपडेट की गई है।",
            False,
            datetime.datetime.now().isoformat(timespec="seconds")
        )
    )

    c.commit()

    # ==============================
    # SEND FIREBASE PUSH NOTIFICATION
    # ==============================

    token_row = c.execute(
        """
        SELECT token
        FROM fcm_tokens
        WHERE family_id=?
        """,
        (family_id,)
    ).fetchone()

    if token_row:
        try:
            message = messaging.Message(
                notification=messaging.Notification(
                    title="💰 बचत अपडेट",
                    body=f"आपकी {month} महीने की ₹{amount:.2f} बचत अपडेट की गई है।"
                ),
                token=token_row["token"]
            )

            messaging.send(message)

        except Exception as e:
            print("FCM notification error:", e)

    c.close()

    return jsonify(
        ok=True
    )
# ==================================================
# ADD SAVING DEBIT / WITHDRAWAL
# ==================================================

@app.post("/api/saving-debits")
def add_saving_debit():

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    try:
        family_id = int(d.get("family_id"))
        amount = float(d.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify(
            error="डेबिट जानकारी सही दें"
        ), 400

    if amount <= 0:
        return jsonify(
            error="डेबिट राशि सही दें"
        ), 400

    c = conn()

    family = c.execute(
        """
        SELECT id, name
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

    deposit_row = c.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM savings
        WHERE family_id=?
        """,
        (family_id,)
    ).fetchone()

    debit_row = c.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM saving_debits
        WHERE family_id=?
        """,
        (family_id,)
    ).fetchone()

    available = float(deposit_row["total"] or 0) - float(debit_row["total"] or 0)

    if amount > available:
        c.close()
        return jsonify(
            error=f"उपलब्ध बचत ₹{available:.2f} है। इससे ज्यादा डेबिट नहीं कर सकते।"
        ), 400

    entry_date = d.get(
        "date",
        datetime.date.today().isoformat()
    )

    reason = (
        d.get("reason") or ""
    ).strip()

    c.execute(
        """
        INSERT INTO saving_debits
        (family_id, amount, date, reason)
        VALUES (?, ?, ?, ?)
        """,
        (
            family_id,
            amount,
            entry_date,
            reason
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# GET SAVING DEBITS
# ==================================================

@app.get("/api/saving-debits")
def get_saving_debits():

    error = admin_required()

    if error:
        return error

    c = conn()

    rows = c.execute(
        """
        SELECT d.*, f.name family
        FROM saving_debits d
        JOIN families f
        ON f.id=d.family_id
        ORDER BY d.id DESC
        """
    ).fetchall()

    c.close()

    return jsonify([
        dict(x)
        for x in rows
    ])


# ==================================================
# UPDATE SAVING DEBIT
# ==================================================

@app.put("/api/saving-debits/<int:did>")
def update_saving_debit(did):

    error = admin_required()

    if error:
        return error

    d = request.json or {}

    try:
        family_id = int(d.get("family_id"))
        amount = float(d.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify(
            error="डेबिट जानकारी सही दें"
        ), 400

    if amount <= 0:
        return jsonify(
            error="डेबिट राशि सही दें"
        ), 400

    c = conn()

    old = c.execute(
        """
        SELECT id, family_id, amount, date, reason
        FROM saving_debits
        WHERE id=?
        """,
        (did,)
    ).fetchone()

    if not old:
        c.close()
        return jsonify(
            error="डेबिट एंट्री नहीं मिली"
        ), 404

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

    deposit_row = c.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM savings
        WHERE family_id=?
        """,
        (family_id,)
    ).fetchone()

    debit_row = c.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM saving_debits
        WHERE family_id=?
          AND id<>?
        """,
        (family_id, did)
    ).fetchone()

    available = (
        float(deposit_row["total"] or 0)
        -
        float(debit_row["total"] or 0)
    )

    if amount > available:
        c.close()
        return jsonify(
            error=f"उपलब्ध बचत ₹{available:.2f} है। इससे ज्यादा डेबिट नहीं कर सकते।"
        ), 400

    entry_date = (
        d.get("date")
        or old["date"]
        or datetime.date.today().isoformat()
    )

    reason = (
        d.get("reason")
        if d.get("reason") is not None
        else (old["reason"] or "")
    ).strip()

    c.execute(
        """
        UPDATE saving_debits
        SET family_id=?, amount=?, date=?, reason=?
        WHERE id=?
        """,
        (
            family_id,
            amount,
            entry_date,
            reason,
            did
        )
    )

    c.commit()
    c.close()

    return jsonify(
        ok=True
    )


# ==================================================
# DELETE SAVING DEBIT
# ==================================================

@app.delete("/api/saving-debits/<int:did>")
def delete_saving_debit(did):

    error = admin_required()

    if error:
        return error

    c = conn()

    row = c.execute(
        """
        SELECT id
        FROM saving_debits
        WHERE id=?
        """,
        (did,)
    ).fetchone()

    if not row:
        c.close()
        return jsonify(
            error="डेबिट एंट्री नहीं मिली"
        ), 404

    c.execute(
        """
        DELETE FROM saving_debits
        WHERE id=?
        """,
        (did,)
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
        SELECT id, name
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

    entry_date = d.get(
        "date",
        datetime.date.today().isoformat()
    )

    # ==============================
    # SAVE LOAN
    # ==============================

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
            entry_date
        )
    )

    # ==============================
    # CREATE MEMBER NOTIFICATION
    # ==============================

    c.execute(
        """
        INSERT INTO notifications
        (family_id, title, message, is_read, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            family_id,
            "💳 Loan अपडेट",
            f"आपके परिवार के लिए ₹{amount:.2f} का Loan अपडेट किया गया है। अवधि: {months} महीने।",
            False,
            datetime.datetime.now().isoformat(timespec="seconds")
        )
    )

    c.commit()

    # ==============================
    # SEND FIREBASE PUSH NOTIFICATION
    # ==============================
    token_row = c.execute(
        """
        SELECT token
        FROM fcm_tokens
        WHERE family_id=?
        """,
        (family_id,)
    ).fetchone()

    if token_row:
        try:
            message = messaging.Message(
                notification=messaging.Notification(
                    title="💳 Loan अपडेट",
                    body=f"आपके परिवार के लिए ₹{amount:.2f} का Loan अपडेट किया गया है। अवधि: {months} महीने।"
                ),
                token=token_row["token"]
            )
            messaging.send(message)
        except Exception as e:
            print("FCM loan notification error:", e)

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

    entry_date = d.get(
        "date",
        datetime.date.today().isoformat()
    )

    # ==============================
    # UPDATE LOAN BALANCE
    # ==============================

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

    # ==============================
    # SAVE PAYMENT
    # ==============================

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
            entry_date
        )
    )

    # ==============================
    # CREATE MEMBER NOTIFICATION
    # ==============================

    c.execute(
        """
        INSERT INTO notifications
        (family_id, title, message, is_read, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            l["family_id"],
            "💵 Payment अपडेट",
            f"आपके Loan का ₹{actual_amount:.2f} भुगतान अपडेट किया गया है। बाकी Loan: ₹{new_balance:.2f}",
            False,
            datetime.datetime.now().isoformat(timespec="seconds")
        )
    )

    c.commit()

    # ==============================
    # SEND FIREBASE PUSH NOTIFICATION
    # ==============================
    
    token_row = c.execute(
        """
        SELECT token
        FROM fcm_tokens
        WHERE family_id=?
        """,
        (l["family_id"],)
    ).fetchone()
    
    if token_row:
        try:
    
            if principal == 0 and interest > 0:
                title = "💰 Loan Interest Paid"
                body = f"आपके Loan का ₹{interest:.2f} ब्याज भुगतान अपडेट किया गया है।"
    
            else:
                title = "💵 Payment अपडेट"
                body = f"आपके Loan का ₹{actual_amount:.2f} भुगतान अपडेट किया गया है। बाकी Loan: ₹{new_balance:.2f}"
    
            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body
                ),
                token=token_row["token"]
            )
                        
            messaging.send(message)
        
        except Exception as e:
            print("FCM payment notification error:", e)
        
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

    distribution_date = d.get(
        "date",
        datetime.date.today().isoformat()
    )
    
    distribution_row = c.execute(
        """
        INSERT INTO interest_distributions
        (total_interest, date)
        VALUES (?, ?)
        RETURNING id
        """,
        (
            total,
            distribution_date
        )
    ).fetchone()
    
    distribution_id = distribution_row["id"]
    
    # ==============================
    # SAVE FAMILY-WISE INTEREST CREDIT
    # ==============================
    
    for r in result:
    
        interest_amount = r["interest"]
    
        if interest_amount <= 0:
            continue
    
        c.execute(
            """
            INSERT INTO interest_credits
            (family_id, amount, date, distribution_id)
            VALUES (?, ?, ?, ?)
            """,
            (
                r["id"],
                interest_amount,
                distribution_date,
                distribution_id
            )
        )
    
    c.commit()


    # ==============================
    # CREATE MEMBER NOTIFICATIONS
    # + SEND FIREBASE PUSH
    # ==============================
    for r in result:

        interest_amount = r["interest"]

        if interest_amount <= 0:
            continue

        title = "💰 ब्याज वितरण"
        body = f"आपके परिवार के खाते में ₹{interest_amount:.2f} ब्याज वितरित किया गया है।"

        c.execute(
            """
            INSERT INTO notifications
            (family_id, title, message, is_read, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                r["id"],
                title,
                body,
                False,
                datetime.datetime.now().isoformat(timespec="seconds")
            )
        )

        token_row = c.execute(
            """
            SELECT token
            FROM fcm_tokens
            WHERE family_id=?
            """,
            (r["id"],)
        ).fetchone()

        if token_row:
            try:
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=title,
                        body=body
                    ),
                    token=token_row["token"]
                )
                messaging.send(message)
            except Exception as e:
                print("FCM interest distribution notification error:", e)

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
