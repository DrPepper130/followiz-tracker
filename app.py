import os
import sqlite3
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

FOLLOWIZ_API_KEY = os.environ.get("FOLLOWIZ_API_KEY")
FOLLOWIZ_API_URL = "https://followiz.com/api/v2"
DB_PATH = "orders.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sellapp_order_id TEXT NOT NULL,
            followiz_order_id TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

init_db()

@app.route("/")
def home():
    return "followiz tracker is live ✅"

# ---------- 1) SELL.APP WEBHOOK ----------
@app.route("/sellapp-webhook", methods=["POST"])
def sellapp_webhook():
    payload = request.get_json(silent=True) or {}
    data = payload.get("data") or {}
    sellapp_id = data.get("id")  # 2566803 in your example
    if not sellapp_id:
        return jsonify({"error": "no sellapp id"}), 400

    # pull product info from webhook
    variants = data.get("product_variants") or []
    qty = 1
    if variants:
        # they bought 1000 in your example
        qty = variants[0].get("quantity", 1)

    # get customer IG/URL from additional_information (your webhook has this)
    ig_link = None
    if variants and variants[0].get("additional_information"):
        for field in variants[0]["additional_information"]:
            if "instagram" in field.get("label", "").lower():
                ig_link = field.get("value")
                break

    # at this point we must create the Followiz order
    # you'll need to know which Followiz service to use; hardcode for now
    FOLLOWIZ_SERVICE_ID = os.environ.get("FOLLOWIZ_SERVICE_ID")  # set on Render

    if not FOLLOWIZ_API_KEY or not FOLLOWIZ_SERVICE_ID:
        return jsonify({"error": "FOLLOWIZ_API_KEY or FOLLOWIZ_SERVICE_ID not set"}), 500

    # call followiz: action=add
    fw_res = requests.post(FOLLOWIZ_API_URL, data={
        "key": FOLLOWIZ_API_KEY,
        "action": "add",
        "service": FOLLOWIZ_SERVICE_ID,
        "link": ig_link or "https://instagram.com",  # fallback so it doesn't fail
        "quantity": qty,
    })
    fw_json = fw_res.json()

    followiz_order_id = fw_json.get("order")
    if not followiz_order_id:
        # followiz failed – you might want to log payload or return 500
        return jsonify({"error": "followiz did not return order", "fw": fw_json}), 500

    # save mapping
    conn = get_db()
    conn.execute(
        "INSERT INTO orders (sellapp_order_id, followiz_order_id) VALUES (?, ?)",
        (str(sellapp_id), str(followiz_order_id))
    )
    conn.commit()

    return jsonify({
        "ok": True,
        "sellapp_order_id": sellapp_id,
        "followiz_order_id": followiz_order_id
    })

# ---------- 2) CUSTOMER LOOKUP ----------
@app.route("/api/order-status", methods=["GET"])
def order_status():
    sellapp_id = request.args.get("order")
    if not sellapp_id:
        return jsonify({"error": "order query param required"}), 400

    conn = get_db()
    cur = conn.execute(
        "SELECT followiz_order_id FROM orders WHERE sellapp_order_id = ?",
        (sellapp_id,)
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "Order not found"}), 404

    followiz_id = row["followiz_order_id"]

    r = requests.post(FOLLOWIZ_API_URL, data={
        "key": FOLLOWIZ_API_KEY,
        "action": "status",
        "orders": str(followiz_id)
    })
    fw = r.json()
    provider_data = fw.get(str(followiz_id))
    if not provider_data:
        return jsonify({"error": "Provider did not return this order"}), 502

    return jsonify({
        "sellapp_order_id": sellapp_id,
        "followiz_order_id": followiz_id,
        "status": provider_data.get("status"),
        "remains": provider_data.get("remains"),
        "start_count": provider_data.get("start_count"),
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
