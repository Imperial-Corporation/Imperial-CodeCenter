"""Parcours de graphe en largeur (BFS) et en profondeur (DFS)."""

from collections import deque

Graph = dict[str, list[str]]


def bfs(graph: Graph, start: str) -> list[str]:
    """Visite les sommets par distance croissante depuis `start`."""
    seen, order, queue = {start}, [], deque([start])
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return order


def dfs(graph: Graph, node: str, seen: set[str] | None = None) -> list[str]:
    """Visite récursive : va au bout d'une branche avant de revenir."""
    seen = set() if seen is None else seen
    seen.add(node)
    order = [node]
    for neighbor in graph.get(node, []):
        if neighbor not in seen:
            order += dfs(graph, neighbor, seen)
    return order


if __name__ == "__main__":
    graph = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
    print(bfs(graph, "A"))  # ['A', 'B', 'C', 'D']
    print(dfs(graph, "A"))  # ['A', 'B', 'D', 'C']
