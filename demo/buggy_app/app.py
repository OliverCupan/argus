"""
Demo App — A deliberately buggy Flask API for testing Argus.

Planted bugs:
1. SQL injection in /users endpoint         (string interpolation — no parameterisation)
2. Hardcoded API key in source              (line 18 — committed secret)
3. O(n²) loop in /stats endpoint            (unbounded nested loop)
4. Unhandled None / missing keys in /process (no input validation → KeyError/TypeError → 500)
5. Failing test in test_app.py              (test_process_missing_key expects 400, gets 500)
"""

import os
import sqlite3
from functools import wraps

from flask import Flask, request, jsonify

API_KEY = "sk-prod-a8f3k2m5n7p9q1r4t6u8w0x2y4z6"
DATABASE = "app.db"

app = Flask(__name__)


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if provided != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def get_db():
    conn = sqlite3.connect(DATABASE)
    return conn


def calculate_average(numbers):
    if not numbers:
        return 0
    return sum(numbers) / len(numbers)


@app.route("/users")
@require_api_key
def get_users():
    """Get users by name filter."""
    name = request.args.get("name", "")

    conn = get_db()
    try:
        # BUG 1: SQL injection — user input directly interpolated into query string
        cursor = conn.execute(
            f"SELECT * FROM users WHERE name = '{name}'"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    users = [{"id": row[0], "name": row[1]} for row in rows]
    return jsonify(users)


@app.route("/stats")
@require_api_key
def get_stats():
    """Calculate pairwise similarity scores."""
    conn = get_db()
    try:
        items = conn.execute("SELECT id, value FROM items").fetchall()
    finally:
        conn.close()

    # BUG 3: O(n²) nested loop — unbounded, no result cap
    results = []
    for i in items:
        for j in items:
            score = abs(i[1] - j[1])
            results.append({"item_a": i[0], "item_b": j[0], "score": score})

    return jsonify(results)


@app.route("/process", methods=["POST"])
@require_api_key
def process_data():
    """Process incoming data payload."""
    data = request.get_json()

    # BUG 4: No None guard — crashes with TypeError if body is absent or non-JSON
    # BUG 4: No key validation — crashes with KeyError if 'value' or 'label' missing
    result = data["value"] * 2
    label = data["label"].upper()

    return jsonify({"result": result, "label": label})


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/hello")
def hello():
    return jsonify({"message": "Hello, World!"})


@app.route("/average", methods=["POST"])
def average():
    """Calculate the average of a list of numbers."""
    data = request.get_json()
    if data is None:
        return jsonify({"error": "Invalid or missing JSON"}), 400
    if "numbers" not in data:
        return jsonify({"error": "Missing 'numbers' key"}), 400
    numbers = data["numbers"]
    if not isinstance(numbers, list):
        return jsonify({"error": "'numbers' must be a list"}), 400
    for item in numbers:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return jsonify({"error": "All items in 'numbers' must be numeric (int or float)"}), 400
    result = calculate_average(numbers)
    return jsonify({"average": result}), 200


if __name__ == "__main__":
    app.run(debug=True)
