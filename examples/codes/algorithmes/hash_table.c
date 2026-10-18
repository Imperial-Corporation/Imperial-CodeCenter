/* Table de hachage en C : chaînage séparé, clés texte, valeurs entières. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BUCKETS 16

typedef struct Entry {
    char *key;
    int value;
    struct Entry *next;
} Entry;

static Entry *table[BUCKETS];

/* Fonction de hachage djb2. */
static unsigned hash(const char *key) {
    unsigned h = 5381;
    while (*key) h = h * 33 + (unsigned char)*key++;
    return h % BUCKETS;
}

void put(const char *key, int value) {
    unsigned index = hash(key);
    for (Entry *e = table[index]; e != NULL; e = e->next) {
        if (strcmp(e->key, key) == 0) {
            e->value = value;
            return;
        }
    }
    Entry *entry = malloc(sizeof *entry);
    if (entry == NULL) exit(EXIT_FAILURE);
    entry->key = strdup(key);
    entry->value = value;
    entry->next = table[index];
    table[index] = entry;
}

int get(const char *key, int *value) {
    for (Entry *e = table[hash(key)]; e != NULL; e = e->next) {
        if (strcmp(e->key, key) == 0) {
            *value = e->value;
            return 1;
        }
    }
    return 0;
}

int main(void) {
    int value;
    put("un", 1);
    put("deux", 2);
    put("un", 10); /* remplace la valeur */
    if (get("un", &value)) printf("un = %d\n", value);
    printf("trois présent : %d\n", get("trois", &value));
    return 0;
}
