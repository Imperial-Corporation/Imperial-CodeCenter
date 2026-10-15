"""SQLite avec le module standard : requêtes paramétrées et transactions."""

import sqlite3

connection = sqlite3.connect(":memory:")
connection.row_factory = sqlite3.Row

with connection:  # valide la transaction, ou l'annule en cas d'erreur
    connection.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, age INTEGER)"
    )
    connection.executemany(
        "INSERT INTO users (name, age) VALUES (?, ?)",
        [("Ada", 36), ("Linus", 28), ("Grace", 45)],
    )

# Toujours des paramètres (?), jamais de concaténation : protège de l'injection SQL.
minimum_age = 30
for row in connection.execute("SELECT name, age FROM users WHERE age >= ? ORDER BY age", (minimum_age,)):
    print(row["name"], row["age"])

connection.close()
