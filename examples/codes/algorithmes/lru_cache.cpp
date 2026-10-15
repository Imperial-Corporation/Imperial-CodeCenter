// Cache LRU en C++17 : table de hachage + liste doublement chaînée, O(1).
#include <iostream>
#include <list>
#include <optional>
#include <string>
#include <unordered_map>

class LruCache {
public:
    explicit LruCache(std::size_t capacity) : capacity_(capacity) {}

    std::optional<int> get(const std::string& key) {
        auto it = index_.find(key);
        if (it == index_.end()) return std::nullopt;
        order_.splice(order_.begin(), order_, it->second);  // le plus récent en tête
        return it->second->second;
    }

    void put(const std::string& key, int value) {
        if (auto it = index_.find(key); it != index_.end()) {
            it->second->second = value;
            order_.splice(order_.begin(), order_, it->second);
            return;
        }
        if (order_.size() == capacity_) {
            index_.erase(order_.back().first);
            order_.pop_back();
        }
        order_.emplace_front(key, value);
        index_[key] = order_.begin();
    }

private:
    std::size_t capacity_;
    std::list<std::pair<std::string, int>> order_;
    std::unordered_map<std::string, std::list<std::pair<std::string, int>>::iterator> index_;
};

int main() {
    LruCache cache(2);
    cache.put("a", 1);
    cache.put("b", 2);
    cache.get("a");
    cache.put("c", 3);  // évince "b"
    std::cout << cache.get("b").has_value() << " " << cache.get("a").value() << "\n";
}
