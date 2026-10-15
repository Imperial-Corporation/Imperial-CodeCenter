// Debounce et throttle : limiter la fréquence d'appel d'une fonction (saisie, scroll…).

export function debounce(fn, delay = 250) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}

export function throttle(fn, interval = 200) {
    let last = 0;
    return (...args) => {
        const now = Date.now();
        if (now - last >= interval) {
            last = now;
            fn(...args);
        }
    };
}

// Exemple : ne lancer la recherche que 300 ms après la dernière frappe.
const input = document.querySelector("#search");
if (input) {
    input.addEventListener("input", debounce((event) => console.log("Recherche :", event.target.value), 300));
}
