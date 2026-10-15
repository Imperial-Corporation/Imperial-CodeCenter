-- Schéma PostgreSQL d'exemple : utilisateurs, rôles et index utiles.
CREATE TABLE IF NOT EXISTS roles (
    id   SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email      TEXT NOT NULL CHECK (position('@' in email) > 1),
    role_id    SMALLINT NOT NULL REFERENCES roles(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

-- Unicité insensible à la casse, uniquement pour les comptes actifs.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_active
    ON users (lower(email))
    WHERE deleted_at IS NULL;

-- Nombre d'utilisateurs actifs par rôle.
SELECT r.name, COUNT(u.id) AS total
FROM roles r
LEFT JOIN users u ON u.role_id = r.id AND u.deleted_at IS NULL
GROUP BY r.name
ORDER BY total DESC;
