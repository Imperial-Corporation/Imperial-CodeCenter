// Exemple Express : serveur JSON avec middleware de journalisation et route paramétrée.
// Installer : npm install express   —   Lancer : node express_server.js
const express = require("express");

const app = express();
app.use(express.json());

app.use((req, res, next) => {
    const start = Date.now();
    res.on("finish", () => {
        console.log(`${req.method} ${req.originalUrl} ${res.statusCode} ${Date.now() - start} ms`);
    });
    next();
});

const users = new Map([[1, { id: 1, name: "Ada" }]]);

app.get("/users/:id", (req, res) => {
    const user = users.get(Number(req.params.id));
    if (!user) return res.status(404).json({ error: "Utilisateur introuvable" });
    res.json(user);
});

app.post("/users", (req, res) => {
    const name = String(req.body?.name ?? "").trim();
    if (!name) return res.status(400).json({ error: "Le nom est obligatoire" });
    const user = { id: users.size + 1, name };
    users.set(user.id, user);
    res.status(201).json(user);
});

app.listen(3000, () => console.log("Serveur sur http://127.0.0.1:3000"));
