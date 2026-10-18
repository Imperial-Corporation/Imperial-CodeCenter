# Limiteur de débit à fenêtre glissante, par clé (ex. adresse IP).
class RateLimiter
  def initialize(limit:, window:)
    @limit = limit
    @window = window
    @hits = Hash.new { |hash, key| hash[key] = [] }
  end

  # Retourne true si la requête est autorisée.
  def allow?(key, now = Time.now.to_f)
    hits = @hits[key]
    hits.reject! { |time| time <= now - @window }
    return false if hits.size >= @limit

    hits << now
    true
  end
end

limiter = RateLimiter.new(limit: 3, window: 10)
results = 5.times.map { limiter.allow?("127.0.0.1", 100.0) }
puts results.inspect # [true, true, true, false, false]
puts limiter.allow?("127.0.0.1", 111.0) # true : la fenêtre a expiré
