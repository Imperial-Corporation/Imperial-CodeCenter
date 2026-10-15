"""Exemple Flask : petite API JSON avec validation et gestion d'erreurs."""

from flask import Flask, jsonify, request

app = Flask(__name__)
notes = []


@app.errorhandler(400)
def bad_request(error):
    return jsonify(error=str(error.description)), 400


@app.get("/notes")
def list_notes():
    return jsonify(notes)


@app.post("/notes")
def add_note():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not str(data.get("text", "")).strip():
        return jsonify(error="Le champ 'text' est obligatoire"), 400
    note = {"id": len(notes) + 1, "text": data["text"].strip()}
    notes.append(note)
    return jsonify(note), 201


if __name__ == "__main__":
    app.run(debug=True)
