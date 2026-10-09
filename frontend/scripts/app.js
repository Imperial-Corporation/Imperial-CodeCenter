async function checkHealth() {
    const status = document.querySelector("#status");
    const details = document.querySelector("#details");

    try {
        const response = await fetch("/api/health");

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        status.textContent = "Système opérationnel";
        details.textContent = data.rust;
    } catch (error) {
        status.textContent = "Erreur système";
        details.textContent = error.message;
    }
}

checkHealth();