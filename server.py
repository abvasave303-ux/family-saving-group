from flask import Flask, request, jsonify, send_from_directory, session
import sqlite3, os, hashlib, datetime

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
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      mobile TEXT DEFAULT '', pin TEXT DEFAULT '1234',
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS savings(
      id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL,
      month TEXT NOT NULL, amount REAL NOT NULL, date TEXT NOT NULL,
      FOREIGN KEY(family_id) REFERENCES families(id)
    );
    CREATE TABLE IF NOT EXISTS loans(
      id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL,
      original REAL NOT NULL, principal REAL NOT NULL, rate REAL DEFAULT 2,
      months INTEGER DEFAULT 12, date TEXT NOT NULL,
      FOREIGN KEY(family_id) REFERENCES families(id)
    );
    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT, loan_id INTEGER NOT NULL,
      family_id INTEGER NOT NULL, amount REAL NOT NULL, interest REAL NOT NULL,
      principal REAL NOT NULL, date TEXT NOT NULL,
      FOREIGN KEY(loan_id) REFERENCES loans(id),
      FOREIGN KEY(family_id) REFERENCES families(id)
    );
    CREATE TABLE IF NOT EXISTS interest_distributions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, total_interest REAL NOT NULL,
      date TEXT NOT NULL
    );
    """)
    n = c.execute("SELECT COUNT(*) n FROM families").fetchone()["n"]
    if n == 0:
        today = datetime.date.today().isoformat()
        for i in range(1,24):
            c.execute("INSERT INTO families(name,created_at) VALUES(?,?)",
                      (f"परिवार {i}", today))
    c.commit(); c.close()

@app.route("/")
def index(): return send_from_directory("static","index.html")

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    pin = os.environ.get("ADMIN_PIN", "1234")
    if str(data.get("pin", "")) != pin:
        return jsonify(error="गलत PIN"), 401
    session["admin"] = True
    return jsonify(ok=True)

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(ok=True)

@app.route("/api/me")
def me():
    return jsonify(admin=bool(session.get("admin")))

@app.before_request
def protect_api():
    if request.path.startswith("/api/") and request.path not in ("/api/login", "/api/me") and not session.get("admin"):
        return jsonify(error="Login required"), 401


@app.get("/api/dashboard")
def dashboard():
    c=conn()
    families=c.execute("SELECT * FROM families ORDER BY id").fetchall()
    savings=c.execute("SELECT COALESCE(SUM(amount),0) x FROM savings").fetchone()["x"]
    loans=c.execute("SELECT COALESCE(SUM(principal),0) x FROM loans").fetchone()["x"]
    interest=c.execute("SELECT COALESCE(SUM(interest),0) x FROM payments").fetchone()["x"]
    return jsonify({
      "families":len(families),"savings":savings,"loans":loans,
      "interest":interest,"available":savings+interest-loans,
      "family_data":[dict(x) for x in families]
    })

@app.get("/api/families")
def get_families():
    c=conn(); fs=c.execute("SELECT * FROM families ORDER BY id").fetchall()
    out=[]
    for f in fs:
        s=c.execute("SELECT COALESCE(SUM(amount),0)x FROM savings WHERE family_id=?",(f["id"],)).fetchone()["x"]
        l=c.execute("SELECT COALESCE(SUM(principal),0)x FROM loans WHERE family_id=?",(f["id"],)).fetchone()["x"]
        out.append({**dict(f),"savings":s,"loan":l})
    return jsonify(out)

@app.post("/api/families")
def add_family():
    d=request.json or {}; name=(d.get("name") or "").strip()
    if not name: return jsonify(error="नाम जरूरी है"),400
    c=conn(); cur=c.execute("INSERT INTO families(name,mobile,created_at) VALUES(?,?,?)",
      (name,d.get("mobile",""),datetime.date.today().isoformat()))
    c.commit(); return jsonify(id=cur.lastrowid)

@app.post("/api/savings")
def add_saving():
    d=request.json or {}; amount=float(d.get("amount",0))
    if amount<=0: return jsonify(error="बचत राशि सही दें"),400
    c=conn(); c.execute("INSERT INTO savings(family_id,month,amount,date) VALUES(?,?,?,?)",
      (int(d["family_id"]),d["month"],amount,d.get("date",datetime.date.today().isoformat())))
    c.commit(); return jsonify(ok=True)
@app.put("/api/savings/<int:sid>")
def update_saving(sid):
    d = request.json or {}
    amount = float(d.get("amount", 0))

    if amount <= 0:
        return jsonify(error="बचत राशि सही दें"), 400

    c = conn()
    row = c.execute(
        "SELECT id FROM savings WHERE id=?",
        (sid,)
    ).fetchone()

    if not row:
        c.close()
        return jsonify(error="बचत एंट्री नहीं मिली"), 404

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
    c = conn()

    row = c.execute(
        "SELECT id FROM savings WHERE id=?",
        (sid,)
    ).fetchone()

    if not row:
        c.close()
        return jsonify(error="बचत एंट्री नहीं मिली"), 404

    c.execute("DELETE FROM savings WHERE id=?", (sid,))
    c.commit()
    c.close()

    return jsonify(ok=True)
    
@app.get("/api/savings")
def savings():
    c=conn(); rows=c.execute("""SELECT s.*,f.name family FROM savings s
      JOIN families f ON f.id=s.family_id ORDER BY s.id DESC""").fetchall()
    return jsonify([dict(x) for x in rows])

@app.post("/api/loans")
def add_loan():
    d=request.json or {}; amount=float(d.get("amount",0))
    if amount<=0:return jsonify(error="लोन राशि सही दें"),400
    c=conn(); c.execute("""INSERT INTO loans(family_id,original,principal,rate,months,date)
      VALUES(?,?,?,?,?,?)""",(int(d["family_id"]),amount,amount,2,int(d.get("months",12)),
      d.get("date",datetime.date.today().isoformat())))
    c.commit(); return jsonify(ok=True)

@app.get("/api/loans")
def loans():
    c=conn(); rows=c.execute("""SELECT l.*,f.name family FROM loans l
      JOIN families f ON f.id=l.family_id ORDER BY l.id DESC""").fetchall()
    return jsonify([dict(x) for x in rows])

@app.post("/api/payments")
def payment():
    d=request.json or {}; amount=float(d.get("amount",0)); lid=int(d["loan_id"])
    c=conn(); l=c.execute("SELECT * FROM loans WHERE id=?",(lid,)).fetchone()
    if not l or amount<=0:return jsonify(error="भुगतान जानकारी सही दें"),400
    # Prototype rule: current month's interest at 2%, then principal.
    interest=min(l["principal"]*2/100, amount)
    principal=amount-interest
    principal=min(principal,l["principal"])
    c.execute("UPDATE loans SET principal=? WHERE id=?",(l["principal"]-principal,lid))
    c.execute("""INSERT INTO payments(loan_id,family_id,amount,interest,principal,date)
      VALUES(?,?,?,?,?,?)""",(lid,l["family_id"],amount,interest,principal,
      d.get("date",datetime.date.today().isoformat())))
    c.commit(); return jsonify(ok=True,interest=interest,principal=principal)

@app.get("/api/payments")
def payments():
    c=conn(); rows=c.execute("""SELECT p.*,f.name family FROM payments p
      JOIN families f ON f.id=p.family_id ORDER BY p.id DESC""").fetchall()
    return jsonify([dict(x) for x in rows])

@app.post("/api/interest-distribution")
def distribution():
    d=request.json or {}; total=float(d.get("total_interest",0))
    if total<=0:return jsonify(error="ब्याज राशि सही दें"),400
    c=conn(); total_s=c.execute("SELECT COALESCE(SUM(amount),0)x FROM savings").fetchone()["x"]
    if total_s<=0:return jsonify(error="पहले बचत एंट्री करें"),400
    rows=c.execute("""SELECT f.id,f.name,COALESCE(SUM(s.amount),0) savings
      FROM families f LEFT JOIN savings s ON s.family_id=f.id GROUP BY f.id ORDER BY f.id""").fetchall()
    result=[{**dict(r),"share":(r["savings"]/total_s),"interest":total*r["savings"]/total_s} for r in rows]
    c.execute("INSERT INTO interest_distributions(total_interest,date) VALUES(?,?)",
      (total,d.get("date",datetime.date.today().isoformat())))
    c.commit(); return jsonify(total_savings=total_s,result=result)

@app.get("/api/family/<int:fid>/passbook")
def passbook(fid):
    c=conn(); f=c.execute("SELECT * FROM families WHERE id=?",(fid,)).fetchone()
    if not f:return jsonify(error="परिवार नहीं मिला"),404
    s=c.execute("SELECT * FROM savings WHERE family_id=? ORDER BY id DESC",(fid,)).fetchall()
    p=c.execute("SELECT * FROM payments WHERE family_id=? ORDER BY id DESC",(fid,)).fetchall()
    l=c.execute("SELECT * FROM loans WHERE family_id=? ORDER BY id DESC",(fid,)).fetchall()
    return jsonify(family=dict(f),savings=[dict(x) for x in s],
      payments=[dict(x) for x in p],loans=[dict(x) for x in l])

@app.get("/api/export/<kind>")
def export_data(kind):
    # JSON export keeps the prototype dependency-free; Excel/CSV can be added in production.
    c=conn()
    if kind=="families": q="SELECT * FROM families"
    elif kind=="savings": q="SELECT s.*,f.name family FROM savings s JOIN families f ON f.id=s.family_id"
    elif kind=="loans": q="SELECT l.*,f.name family FROM loans l JOIN families f ON f.id=l.family_id"
    else:return jsonify(error="unknown export"),400
    return jsonify([dict(x) for x in c.execute(q).fetchall()])

# Initialize database when the app starts
init_db()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8000)),debug=False)
