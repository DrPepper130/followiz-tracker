import os
import sqlite3
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)
app.config["PROPAGATE_EXCEPTIONS"] = True
app.config["DEBUG"] = True

# --------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------
FOLLOWIZ_API_KEY = os.environ.get("FOLLOWIZ_API_KEY")
FOLLOWIZ_API_URL = "https://followiz.com/api/v2"
FOLLOWIZ_SERVICE_ID = os.environ.get("FOLLOWIZ_SERVICE_ID")  # optional, for auto-create
DB_PATH = "orders.db"


# --------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------
@app.after_request
def add_cors_headers(response):
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


# manual add (you already used this)
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


# customer lookup (Framer calls this)
@app.route("/api/order-status", methods=["GET", "OPTIONS"])
def order_status():
    if request.method == "OPTIONS":
        return "", 204

    sellapp_id = request.args.get("order")
    if not sellapp_id:
        return jsonify({"error": "order query param required"}), 400

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

    try:
        fw = r.json()
    except ValueError:
        return jsonify({"error": "Provider returned non-JSON"}), 502

    provider_data = fw.get(str(followiz_id))
    if not provider_data:
        # Followiz didn’t know that order id
        return jsonify({"status": None, "start_count": None, "remains": None})

    return jsonify(
        {
            "status": provider_data.get("status"),
            "start_count": provider_data.get("start_count"),
            "remains": provider_data.get("remains"),
        }
    )


# --------------------------------------------------------------------
# NEW: Sell.app webhook → auto-create on Followiz → save mapping
# --------------------------------------------------------------------
@app.route("/api/sellapp-webhook", methods=["POST", "OPTIONS"])
def sellapp_webhook():
    if request.method == "OPTIONS":
        return "", 204

    payload = request.get_json(silent=True) or {}
    event = payload.get("event")
    data = payload.get("data") or {}

    # we only care about order.paid
    if event != "order.paid":
        return jsonify({"ok": False, "reason": "unsupported event"}), 400

    sellapp_order_id = str(data.get("id"))
    if not sellapp_order_id:
        return jsonify({"ok": False, "reason": "no sellapp id"}), 400

    if not FOLLOWIZ_API_KEY:
        return jsonify({"ok": False, "reason": "FOLLOWIZ_API_KEY not set"}), 500

    if not FOLLOWIZ_SERVICE_ID:
        # you didn’t set a service id, so we can’t auto-create.
        # but we can at least tell you the sellapp id that came in.
        return jsonify({
            "ok": False,
            "reason": "FOLLOWIZ_SERVICE_ID not set on server",
            "sellapp_order_id": sellapp_order_id
        }), 500

    # try to get the link / username from additional_information
    link = None
    quantity = 1

    product_variants = data.get("product_variants") or []
    if product_variants:
        pv = product_variants[0]
        quantity = pv.get("quantity", 1)
        add_info = pv.get("additional_information") or []
        for field in add_info:
            # very rough: pick the first value
            if field.get("value"):
                link = field["value"]
                break

    if not link:
        # fallback so followiz doesn't crash
        link = "https://instagram.com"

    # create the Followiz order
    try:
        fw_res = requests.post(
            FOLLOWIZ_API_URL,
            data={
                "key": FOLLOWIZ_API_KEY,
                "action": "add",
                "service": FOLLOWIZ_SERVICE_ID,
                "link": link,
                "quantity": quantity,
            },
            timeout=10,
        )
        fw_json = fw_res.json()
    except Exception as e:
        return jsonify({"ok": False, "reason": "error calling followiz", "details": str(e)}), 502

    followiz_order_id = fw_json.get("order")
    if not followiz_order_id:
        return jsonify({"ok": False, "reason": "followiz did not return order", "provider": fw_json}), 502

    # save mapping
    conn = get_db()
    conn.execute(
        "INSERT INTO orders (sellapp_order_id, followiz_order_id) VALUES (?, ?)",
        (sellapp_order_id, str(followiz_order_id)),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "sellapp_order_id": sellapp_order_id,
        "followiz_order_id": followiz_order_id
    })


# --------------------------------------------------------------------
# LOCAL RUN
# --------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

