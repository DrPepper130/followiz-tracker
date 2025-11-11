import os
import sqlite3
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# --------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------
FOLLOWIZ_API_KEY = os.environ.get("FOLLOWIZ_API_KEY")
FOLLOWIZ_API_URL = "https://followiz.com/api/v2"
DB_PATH = "orders.db"


# --------------------------------------------------------------------
# CORS (so Framer / your site can fetch this)
# --------------------------------------------------------------------
@app.after_request
def add_cors_headers(response):
    # allow all origins for now; you can lock it to your domain later
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# --------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------
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


# --------------------------------------------------------------------
# ROUTES
# --------------------------------------------------------------------
@app.route("/")
def home():
    return "followiz tracker is live ✅"


@app.route("/api/add-order", methods=["POST", "OPTIONS"])
def add_order():
    if request.method == "OPTIONS":
        # for CORS preflight
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


@app.route("/api/order-status", methods=["GET", "OPTIONS"])
def order_status():
    if request.method == "OPTIONS":
        return "", 204

    sellapp_id = request.args.get("order")
    if not sellapp_id:
        return jsonify({"error": "order query param required"}), 400

    # look up the mapping we saved earlier
    conn = get_db()
    cur = conn.execute(
        "SELECT followiz_order_id FROM orders WHERE sellapp_order_id = ?",
        (str(sellapp_id),),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Order not found"}), 404

    followiz_id = row["followiz_order_id"]

    if not FOLLOWIZ_API_KEY:
        return jsonify({"error": "FOLLOWIZ_API_KEY not set"}), 500

    # call Followiz using their "multiple orders status" format with 1 order
    try:
        r = requests.post(
            FOLLOWIZ_API_URL,
            data={
                "key": FOLLOWIZ_API_KEY,
                "action": "status",
                "orders": str(followiz_id),
            },
            timeout=10,
        )
    except requests.RequestException as e:
        return jsonify({"error": "Failed to contact provider", "details": str(e)}), 502

    # Followiz returns an object keyed by the order ID
    try:
        fw = r.json()
    except ValueError:
        return jsonify({"error": "Provider returned non-JSON"}), 502

    provider_data = fw.get(str(followiz_id))
    if not provider_data:
        # this happens when the Followiz order ID isn't valid
        return jsonify({"status": None, "start_count": None, "remains": None})

    return jsonify(
        {
            "status": provider_data.get("status"),
            "start_count": provider_data.get("start_count"),
            "remains": provider_data.get("remains"),
        }
    )


# --------------------------------------------------------------------
# LOCAL RUN
# --------------------------------------------------------------------
if __name__ == "__main__":
    # for local testing: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
