"""
Demo App — A deliberately buggy Flask API for testing Argus.

Planted bugs:
1. SQL injection in /users endpoint
2. Hardcoded API key in source
3. O(n²) loop in /stats endpoint
4. Unhandled None in /process endpoint
5. Failing test in test_app.py
"""

import sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)

# BUG 1: Hardcoded secret in source code
API_KEY = "sk-prod-a8f3k2m5n7p9q1r4t6u8w0x2y4z6"
DATABASE = "app.db"


def get_db():
    conn = sqlite3.connect(DATABASE)
    return conn


@app.route("/users")
def get_users():
    """Get users by name filter."""
    name = request.args.get("name", "")

    conn = get_db()
    # Fixed: Use parameterized query to prevent SQL injection
    cursor = conn.execute("SELECT * FROM users WHERE name = ?", (name,))
    users = cursor.fetchall()
    conn.close()

    return jsonify(users)


@app.route("/stats")
def get_stats():
    """Calculate pairwise similarity scores."""
    conn = get_db()
    items = conn.execute("SELECT * FROM items").fetchall()
    conn.close()

    # BUG 3: O(n²) nested loop — will be very slow with large datasets
    results = []
    for i in items:
        for j in items:
            score = abs(i[1] - j[1])
            results.append({"item_a": i[0], "item_b": j[0], "score": score})

    return jsonify(results)


@app.route("/process", methods=["POST"])
def process_data():
    """Process incoming data payload."""
    data = request.get_json()

    # BUG 4: No None check — crashes if 'value' key missing
    result = data["value"] * 2
    label = data["label"].upper()

    return jsonify({"result": result, "label": label})


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/hello")
def hello():
    """Simple hello world endpoint."""
    return jsonify({"message": "Hello, World!"})


if __name__ == "__main__":
    app.run(debug=True)
