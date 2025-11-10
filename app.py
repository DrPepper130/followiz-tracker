from flask import Flask, request, jsonify
import requests
import sqlite3
import os

app = Flask(__name__)

FOLLOWIZ_API_KEY = os.environ.get("FOLLOWIZ_API_KEY")  # we’ll set on Render
FOLLOWIZ_API_URL = "https://followiz.com/api/v2"

DB_PATH = "orders.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# create table if not exists (simple)
def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sellapp_order_id TEXT,
            followiz_order_id TEXT
        );
    """)
    conn.commit()
    conn.close()

init_db()

@app.route("/api/order-status", methods=["GET"])
def order_status():
    sellapp_id = request.args.get("order")
    if not sellapp_id:
        return jsonify({"error": "order parameter required"}), 400

    conn = get_db()
    cur = conn.execute(
        "SELECT followiz_order_id FROM orders WHERE sellapp_order_id = ?",
        (sellapp_id,)
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "Order not found"}), 404

    followiz_id = row["followiz_order_id"]

    # call followiz
    r = requests.post(FOLLOWIZ_API_URL, data={
        "key": FOLLOWIZ_API_KEY,
        "action": "status",
        "orders": str(followiz_id)
    })
    fw = r.json()
    data_for_this = fw.get(str(followiz_id))
    if not data_for_this:
        return jsonify({"error": "Provider didn’t return this order"}), 502

    return jsonify({
        "sellapp_order_id": sellapp_id,
        "followiz_order_id": followiz_id,
        "status": data_for_this.get("status"),
        "remains": data_for_this.get("remains"),
        "start_count": data_for_this.get("start_count"),
    })

# Render uses this
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
