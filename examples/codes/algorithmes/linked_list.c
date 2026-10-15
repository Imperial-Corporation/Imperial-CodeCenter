/* Liste chaînée simple en C : ajout en tête, recherche, libération. */
#include <stdio.h>
#include <stdlib.h>

typedef struct Node {
    int value;
    struct Node *next;
} Node;

Node *push_front(Node *head, int value) {
    Node *node = malloc(sizeof *node);
    if (node == NULL) {
        perror("malloc");
        exit(EXIT_FAILURE);
    }
    node->value = value;
    node->next = head;
    return node;
}

int contains(const Node *head, int value) {
    for (; head != NULL; head = head->next) {
        if (head->value == value) return 1;
    }
    return 0;
}

void free_list(Node *head) {
    while (head != NULL) {
        Node *next = head->next;
        free(head);
        head = next;
    }
}

int main(void) {
    Node *list = NULL;
    for (int i = 1; i <= 3; i++) list = push_front(list, i * 10);

    printf("contient 20 : %d\n", contains(list, 20));
    printf("contient 99 : %d\n", contains(list, 99));

    free_list(list);
    return 0;
}
