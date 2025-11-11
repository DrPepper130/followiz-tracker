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
    return "Followiz tracker is live ✅"


# Add mapping manually or from webhook
@app.route("/api/add-order", methods=["POST"])
def add_order():
    data = request.get_json(silent=True) or {}
    sellapp_id = data.get("sellapp_order_id")
    followiz_id = data.get("followiz_order_id")

    if not sellapp_id or not followiz_id:
        return jsonify({"error": "sellapp_order_id and followiz_order_id required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO orders (sellapp_order_id, followiz_order_id) VALUES (?, ?)",
        (sellapp_id, followiz_id)
    )
    conn.commit()
    return jsonify({"ok": True})


# Fetch live Followiz status for any order
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
        "status": provider_data.get("status"),
        "start_count": provider_data.get("start_count"),
        "remains": provider_data.get("remains")
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
