-- Pagination SQL : OFFSET simple, puis pagination par clé (keyset).
CREATE TABLE IF NOT EXISTS articles (
    id    INTEGER PRIMARY KEY,
    titre TEXT NOT NULL
);

INSERT INTO articles (titre) VALUES ('A'), ('B'), ('C'), ('D'), ('E');

-- Page 2 avec 2 éléments par page : simple mais lent sur de grandes tables.
SELECT id, titre FROM articles ORDER BY id LIMIT 2 OFFSET 2;

-- Keyset : on repart du dernier id vu (ici 2), performant grâce à l'index.
SELECT id, titre FROM articles WHERE id > 2 ORDER BY id LIMIT 2;
