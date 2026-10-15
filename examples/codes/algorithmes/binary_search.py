"""Recherche dichotomique : version itérative et utilisation de bisect."""

from bisect import bisect_left


def binary_search(values: list[int], target: int) -> int:
    """Retourne l'indice de `target` dans la liste triée, ou -1."""
    low, high = 0, len(values) - 1
    while low <= high:
        middle = (low + high) // 2
        if values[middle] == target:
            return middle
        if values[middle] < target:
            low = middle + 1
        else:
            high = middle - 1
    return -1


def binary_search_bisect(values: list[int], target: int) -> int:
    index = bisect_left(values, target)
    return index if index < len(values) and values[index] == target else -1


if __name__ == "__main__":
    data = [1, 3, 5, 7, 9, 11]
    assert binary_search(data, 7) == 3
    assert binary_search(data, 4) == -1
    assert binary_search_bisect(data, 11) == 5
    print("ok")
