import os
import sqlite3
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
app.config["DEBUG"] = True
app.config["PROPAGATE_EXCEPTIONS"] = True

FOLLOWIZ_API_KEY = os.environ.get("FOLLOWIZ_API_KEY")
FOLLOWIZ_API_URL = "https://followiz.com/api/v2"
DB_PATH = "orders.db"


# -------------------------------------------------
# CORS
# -------------------------------------------------
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# -------------------------------------------------
# DB
# -------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sellapp_order_id TEXT NOT NULL,
            followiz_order_id TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


init_db()


# -------------------------------------------------
# ROOT
# -------------------------------------------------
@app.route("/")
def home():
    return "followiz tracker is live ✅"


# -------------------------------------------------
# manual add (you used this from PowerShell)
# -------------------------------------------------
@app.route("/api/add-order", methods=["POST", "OPTIONS"])
def add_order():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    sellapp_id = data.get("sellapp_order_id")
    followiz_id = data.get("followiz_order_id")

    if not sellapp_id or not followiz_id:
        return jsonify({"error": "sellapp_order_id and followiz_order_id required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO orders (sellapp_order_id, followiz_order_id) VALUES (?, ?)",
        (str(sellapp_id), str(followiz_id)),
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True})


# -------------------------------------------------
# lookup (Framer uses this)
# -------------------------------------------------
@app.route("/api/order-status", methods=["GET", "OPTIONS"])
def order_status():
    if request.method == "OPTIONS":
        return "", 204

    order_id = request.args.get("order")
    if not order_id:
        return jsonify({"error": "order query param required"}), 400

    conn = get_db()
    # try as Sell.app ID first
    cur = conn.execute(
        "SELECT followiz_order_id FROM orders WHERE sellapp_order_id = ?",
        (str(order_id),),
    )
    row = cur.fetchone()

    # if not found, try as Followiz ID (user pasted provider ID)
    if not row:
        cur = conn.execute(
            "SELECT followiz_order_id FROM orders WHERE followiz_order_id = ?",
            (str(order_id),),
        )
        row = cur.fetchone()

    conn.close()

    if not row:
        return jsonify({"error": "Order not found"}), 404

    followiz_id = row["followiz_order_id"]

    if not FOLLOWIZ_API_KEY:
        return jsonify({"error": "FOLLOWIZ_API_KEY not set"}), 500

    # call followiz: single order status (your screenshot)
    try:
        r = requests.post(
            FOLLOWIZ_API_URL,
            data={
                "key": FOLLOWIZ_API_KEY,
                "action": "status",
                "order": str(followiz_id),  # single-order endpoint
            },
            timeout=10,
        )
        fw = r.json()
    except Exception as e:
        return jsonify({"error": "Failed to contact provider", "details": str(e)}), 502

    return jsonify(
        {
            "status": fw.get("status"),
            "start_count": fw.get("start_count"),
            "remains": fw.get("remains"),
        }
    )


# -------------------------------------------------
# Sell.app webhook (no service id, no provider create)
# -------------------------------------------------
@app.route("/api/sellapp-webhook", methods=["POST", "OPTIONS"])
def sellapp_webhook():
    if request.method == "OPTIONS":
        return "", 204

    payload = request.get_json(silent=True) or {}
    event = payload.get("event")
    data = payload.get("data") or {}

    # we only care about order.paid
    if event != "order.paid":
        return jsonify({"ok": True, "ignored": True})

    sellapp_order_id = str(data.get("id"))
    # try to see if caller already sent us followiz_order_id
    # (you can add it from your script when you POST to this endpoint)
    followiz_order_id = payload.get("followiz_order_id")

    if sellapp_order_id and followiz_order_id:
        conn = get_db()
        conn.execute(
            "INSERT INTO orders (sellapp_order_id, followiz_order_id) VALUES (?, ?)",
            (sellapp_order_id, str(followiz_order_id)),
        )
        conn.commit()
        conn.close()
        return jsonify(
            {
                "ok": True,
                "saved": True,
                "sellapp_order_id": sellapp_order_id,
                "followiz_order_id": followiz_order_id,
            }
        )

    # if we got here, we got the Sell.app order but not the Followiz order.
    # that's fine — respond 200 so Sell.app doesn't retry.
    return jsonify(
        {
            "ok": True,
            "saved": False,
            "sellapp_order_id": sellapp_order_id,
            "reason": "no followiz_order_id provided; call /api/add-order or send it in webhook next time",
        }
    )


# -------------------------------------------------
# local run
# -------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
