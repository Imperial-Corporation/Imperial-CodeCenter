// Cache avec durée de vie (TTL) : les entrées expirent automatiquement.
class TtlCache {
  constructor(ttlMs, now = () => Date.now()) {
    this.ttlMs = ttlMs;
    this.now = now;
    this.store = new Map();
  }

  set(key, value) {
    this.store.set(key, { value, expires: this.now() + this.ttlMs });
  }

  get(key) {
    const entry = this.store.get(key);
    if (!entry) return undefined;
    if (entry.expires <= this.now()) {
      this.store.delete(key); // entrée périmée
      return undefined;
    }
    return entry.value;
  }
}

// Horloge simulée pour une démonstration déterministe.
let time = 0;
const cache = new TtlCache(1000, () => time);
cache.set("a", 42);
console.log(cache.get("a")); // 42
time = 1500;
console.log(cache.get("a")); // undefined
