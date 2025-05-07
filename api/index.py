from flask import Flask, request, jsonify

app = Flask(__name__)

# Simple sanity‐check endpoints
@app.route('/', methods=['GET'])
def home():
    return 'Hello, World!'

@app.route('/about', methods=['GET'])
def about():
    return 'About'

# -----------------------------------------------------------------------------
# NEW: A /run endpoint that accepts JSON, calls your function, and returns JSON
# -----------------------------------------------------------------------------
@app.route('/run', methods=['POST'])
def run_function():
    """
    Expects a JSON body like:
      { "input": { /* arbitrary payload */ } }
    You can adjust the key names or structure however you like.
    """
    # 1) Parse the incoming JSON
    payload = request.get_json(silent=True)
    if not payload or 'input' not in payload:
        return jsonify(error="Request must be JSON with an 'input' key"), 400

    user_input = payload['input']

    # 2) Call your backend logic here.
    #    For example, if you have a function `process_data` in another module:
    # from my_module import process_data
    # result = process_data(user_input)
    #
    # For now, let’s just echo it back:
    result = {
        "echo": user_input
    }

    # 3) Return the result as JSON
    return jsonify(result=result), 200


if __name__ == '__main__':
    # Running this script directly spins up a development server:
    #   $ python index.py
    app.run(debug=True, host='0.0.0.0', port=5000)
