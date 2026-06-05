"""
Demo App — A deliberately buggy Flask API for testing Argus.

Planted bugs (now fixed):
1. SQL injection in /users endpoint         → parameterised query + field projection
2. Hardcoded API key in source              → os.getenv(); enforced on all routes
3. O(n²) loop in /stats endpoint            → result cap of MAX_STATS_RESULTS
4. Unhandled None / missing keys in /process → explicit validation with 400 errors
5. Failing test in test_app.py              → test now passes; null-body case added
"""

import os
import sqlite3
from functools import wraps

from flask import Flask, request, jsonify

app = Flask(__name__)

# API key loaded from environment — never hardcode secrets in source
API_KEY = os.getenv("DEMO_API_KEY", "")
DATABASE = "app.db"

# Safety cap for /stats to prevent memory exhaustion on large tables
MAX_STATS_RESULTS = 1_000


def require_api_key(f):
    """Decorator that enforces X-API-Key header authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        provided = request.headers.get("X-API-Key", "")
        if not API_KEY or provided != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def get_db():
    conn = sqlite3.connect(DATABASE)
    return conn


def calculate_average(numbers):
    """
    Calculate the average of a list of numbers.
    
    Args:
        numbers: List of int/float values
        
    Returns:
        float: The average, or 0 if the list is empty
    """
    if not numbers:
        return 0
    return sum(numbers) / len(numbers)


@app.route("/users")
@require_api_key
def get_users():
    """Get users by name filter.

    FIX 1: Parameterised query prevents SQL injection.
    FIX 5 (info disclosure): Only id and name are returned — no full row dump.
    """
    name = request.args.get("name", "")

    conn = get_db()
    try:
        # Parameterised query — user input never interpolated into SQL
        cursor = conn.execute(
            "SELECT id, name FROM users WHERE name = ?", (name,)
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    users = [{"id": row[0], "name": row[1]} for row in rows]
    return jsonify(users)


@app.route("/stats")
@require_api_key
def get_stats():
    """Calculate pairwise similarity scores.

    FIX 3: Result set is capped at MAX_STATS_RESULTS to prevent O(n²) memory
    exhaustion — with 10k rows the unbounded version would build 100M objects
    before sending a single byte.  The cap converts this from a DoS vector into
    a bounded, predictable response.
    """
    conn = get_db()
    try:
        items = conn.execute("SELECT id, value FROM items").fetchall()
    finally:
        conn.close()

    results = []
    for i in items:
        for j in items:
            if len(results) >= MAX_STATS_RESULTS:
                break
            score = abs(i[1] - j[1])
            results.append({"item_a": i[0], "item_b": j[0], "score": score})
        if len(results) >= MAX_STATS_RESULTS:
            break

    return jsonify(results)


@app.route("/process", methods=["POST"])
@require_api_key
def process_data():
    """Process incoming data payload.

    FIX 4a: Explicit None guard — request.get_json() returns None when the body
             is absent or the Content-Type is not application/json.  Accessing
             None["value"] raises TypeError, not KeyError, so a bare
             `except KeyError` would silently miss it.
    FIX 4b: Explicit key presence checks return 400 instead of letting a
             KeyError bubble up to a 500.
    """
    data = request.get_json()

    if data is None:
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    if "value" not in data:
        return jsonify({"error": "Missing required field: 'value'"}), 400
    if "label" not in data:
        return jsonify({"error": "Missing required field: 'label'"}), 400

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


@app.route("/average", methods=["POST"])
def average():
    """Calculate the average of a list of numbers.
    
    Expects JSON body: {"numbers": [1, 2, 3, ...]}
    Returns: {"average": <result>}
    """
    data = request.get_json()
    
    # Validate JSON was provided
    if data is None:
        return jsonify({"error": "Invalid or missing JSON"}), 400
    
    # Validate 'numbers' key exists
    if "numbers" not in data:
        return jsonify({"error": "Missing 'numbers' key"}), 400
    
    numbers = data["numbers"]
    
    # Validate 'numbers' is a list
    if not isinstance(numbers, list):
        return jsonify({"error": "'numbers' must be a list"}), 400
    
    # Validate all elements are numeric (int or float)
    for item in numbers:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return jsonify({"error": "All items in 'numbers' must be numeric (int or float)"}), 400
    
    result = calculate_average(numbers)
    return jsonify({"average": result}), 200


if __name__ == "__main__":
    app.run(debug=True)
