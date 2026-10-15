/* Arrêt propre sur Ctrl+C en C : le gestionnaire ne fait que lever un drapeau. */
#include <signal.h>
#include <stdio.h>
#include <unistd.h>

static volatile sig_atomic_t stop_requested = 0;

static void on_signal(int signal_number) {
    (void)signal_number;
    stop_requested = 1;  /* seules des opérations async-signal-safe sont permises ici */
}

int main(void) {
    struct sigaction action = {0};
    action.sa_handler = on_signal;
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);

    while (!stop_requested) {
        puts("travail en cours...");
        sleep(1);
    }

    puts("arrêt demandé, nettoyage puis sortie");
    return 0;
}
