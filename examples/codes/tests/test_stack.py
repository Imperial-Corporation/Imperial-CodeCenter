"""Tests pytest : une pile simple et ses cas limites (lancer : pytest -q)."""

import pytest


class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise IndexError("pile vide")
        return self._items.pop()

    def __len__(self):
        return len(self._items)


@pytest.fixture
def stack():
    return Stack()


def test_lifo(stack):
    stack.push(1)
    stack.push(2)
    assert stack.pop() == 2
    assert stack.pop() == 1


def test_pile_vide(stack):
    with pytest.raises(IndexError):
        stack.pop()


@pytest.mark.parametrize("n", [0, 1, 5])
def test_taille(stack, n):
    for i in range(n):
        stack.push(i)
    assert len(stack) == n
