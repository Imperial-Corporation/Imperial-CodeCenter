"""Plus court chemin de Dijkstra avec une file de priorité (heapq)."""

import heapq


def dijkstra(graph: dict[str, dict[str, int]], start: str) -> dict[str, int]:
    """`graph[a][b]` est le poids de l'arête a -> b (poids positifs)."""
    distances = {start: 0}
    queue = [(0, start)]

    while queue:
        distance, node = heapq.heappop(queue)
        if distance > distances.get(node, float("inf")):
            continue  # entrée obsolète
        for neighbor, weight in graph.get(node, {}).items():
            candidate = distance + weight
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                heapq.heappush(queue, (candidate, neighbor))

    return distances


if __name__ == "__main__":
    graph = {"A": {"B": 4, "C": 1}, "C": {"B": 2, "D": 7}, "B": {"D": 1}}
    print(dijkstra(graph, "A"))  # {'A': 0, 'B': 3, 'C': 1, 'D': 4}
