"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const connectionStatus = document.getElementById("connection-status");
    const connectionLabel = document.getElementById("connection-label");

    const searchForm = document.getElementById("search-form");
    const searchInput = document.getElementById("search-input");

    const fileInput = document.getElementById("file-input");
    const dropZone = document.getElementById("drop-zone");
    const fileList = document.getElementById("file-list");
    const fileCounter = document.getElementById("file-counter");
    const message = document.getElementById("message");

    let socket = null;
    let reconnectTimer = null;
    let reconnectDelay = 1000;
    let manuallyClosed = false;

    let selectedFiles = [];

    /* Statut WebSocket */

    function setConnectionStatus(status, label) {
        if (connectionStatus) {
            connectionStatus.dataset.status = status;
        }

        if (connectionLabel) {
            connectionLabel.textContent = label;
        }
    }

    function connectWebSocket() {
        if (manuallyClosed) return;

        if (socket &&
            (socket.readyState === WebSocket.OPEN ||
             socket.readyState === WebSocket.CONNECTING)) {
            return;
        }

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${protocol}//${window.location.host}/ws/status`;

        setConnectionStatus("connecting", "Connexion...");

        try {
            socket = new WebSocket(url);
        } catch (error) {
            setConnectionStatus("disconnected", "Hors ligne");
            scheduleReconnect();
            return;
        }

        socket.addEventListener("open", () => {
            reconnectDelay = 1000;
            setConnectionStatus("connected", "Système joignable");
        });

        socket.addEventListener("message", () => {
            // Réservé aux futurs messages du serveur.
        });

        socket.addEventListener("error", () => {
            setConnectionStatus("disconnected", "Connexion perdue");
        });

        socket.addEventListener("close", () => {
            setConnectionStatus("disconnected", "Hors ligne");
            scheduleReconnect();
        });
    }

    function scheduleReconnect() {
        if (manuallyClosed || reconnectTimer !== null) return;

        reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connectWebSocket();
        }, reconnectDelay);

        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    }

    window.addEventListener("online", () => {
        reconnectDelay = 1000;
        connectWebSocket();
    });

    window.addEventListener("offline", () => {
        setConnectionStatus("disconnected", "Pas de réseau");
    });

    window.addEventListener("beforeunload", () => {
        manuallyClosed = true;

        if (reconnectTimer !== null) {
            window.clearTimeout(reconnectTimer);
        }

        if (socket) {
            socket.close();
        }
    });

    connectWebSocket();

    /* Recherche */

    if (searchForm && searchInput) {
        searchForm.addEventListener("submit", (event) => {
            event.preventDefault();

            const query = searchInput.value.trim();

            if (!query) {
                searchInput.focus();
                showMessage("Entre une recherche.");
                return;
            }

            showMessage("Le moteur de recherche sera bientôt disponible.");
        });
    }

    /* Messages d'interface */

    function showMessage(text) {
        if (!message) return;
        message.textContent = text;
    }

    /* Gestion des fichiers */

    function formatFileSize(bytes) {
        if (bytes < 1024) return `${bytes} o`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
        if (bytes < 1024 * 1024 * 1024) {
            return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
        }

        return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} Go`;
    }

    function getFileExtension(filename) {
        const index = filename.lastIndexOf(".");
        return index >= 0 ? filename.slice(index + 1).toLowerCase() : "file";
    }

    function addFiles(files) {
        let added = 0;

        for (const file of files) {
            const duplicate = selectedFiles.some((existing) =>
                existing.name === file.name &&
                existing.size === file.size &&
                existing.lastModified === file.lastModified
            );

            if (!duplicate) {
                selectedFiles.push(file);
                added++;
            }
        }

        renderFiles();

        if (added > 0) {
            showMessage(
                `${added} fichier${added > 1 ? "s ajoutés" : " ajouté"}. L'envoi au moteur n'est pas encore activé.`
            );
        }
    }

    function removeFile(index) {
        selectedFiles.splice(index, 1);
        renderFiles();
        showMessage("");
    }

    function renderFiles() {
        if (!fileList) return;

        fileList.replaceChildren();

        if (fileCounter) {
            fileCounter.textContent =
                `${selectedFiles.length} fichier${selectedFiles.length !== 1 ? "s" : ""}`;
        }

        fileList.hidden = selectedFiles.length === 0;

        selectedFiles.forEach((file, index) => {
            const card = document.createElement("div");
            card.className = "file-card";

            const icon = document.createElement("div");
            icon.className = "file-icon";
            icon.textContent = getFileExtension(file.name).slice(0, 5);

            const info = document.createElement("div");
            info.className = "file-info";

            const name = document.createElement("span");
            name.className = "file-name";
            name.textContent = file.name;
            name.title = file.name;

            const size = document.createElement("span");
            size.className = "file-size";
            size.textContent = formatFileSize(file.size);

            const removeButton = document.createElement("button");
            removeButton.className = "file-remove";
            removeButton.type = "button";
            removeButton.textContent = "×";
            removeButton.title = "Retirer ce fichier";
            removeButton.setAttribute("aria-label", `Retirer ${file.name}`);

            removeButton.addEventListener("click", () => removeFile(index));

            info.append(name, size);
            card.append(icon, info, removeButton);
            fileList.append(card);
        });
    }

    if (fileInput) {
        fileInput.addEventListener("change", () => {
            addFiles(fileInput.files);
            fileInput.value = "";
        });
    }

    if (dropZone) {
        dropZone.addEventListener("dragover", (event) => {
            event.preventDefault();
            dropZone.classList.add("drag-over");
        });

        dropZone.addEventListener("dragleave", (event) => {
            if (!dropZone.contains(event.relatedTarget)) {
                dropZone.classList.remove("drag-over");
            }
        });

        dropZone.addEventListener("drop", (event) => {
            event.preventDefault();
            dropZone.classList.remove("drag-over");

            if (event.dataTransfer?.files) {
                addFiles(event.dataTransfer.files);
            }
        });
    }
});