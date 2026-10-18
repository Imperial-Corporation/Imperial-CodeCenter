// Pagination par curseur : renvoie une page et le curseur de la suivante.
interface Page<T> {
  items: T[];
  nextCursor: number | null;
}

function paginate<T>(all: T[], cursor = 0, limit = 10): Page<T> {
  const size = Math.max(1, Math.min(limit, 100)); // borne la taille de page
  const start = Math.max(0, cursor);
  const items = all.slice(start, start + size);
  const next = start + size;
  return { items, nextCursor: next < all.length ? next : null };
}

const data = Array.from({ length: 25 }, (_, i) => `item-${i + 1}`);
let cursor: number | null = 0;
while (cursor !== null) {
  const page: Page<string> = paginate(data, cursor, 10);
  console.log(page.items.length, "éléments, suite :", page.nextCursor);
  cursor = page.nextCursor;
}
