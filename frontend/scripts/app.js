"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const API_BASE = "/api/v1";

    const connectionStatus = document.getElementById("connection-status");
    const connectionLabel = document.getElementById("connection-label");

    const searchForm = document.getElementById("search-form");
    const searchInput = document.getElementById("search-input");
    const searchButton = document.getElementById("search-button");

    const fileInput = document.getElementById("file-input");
    const dropZone = document.getElementById("drop-zone");
    const fileList = document.getElementById("file-list");
    const fileCounter = document.getElementById("file-counter");
    const uploadSection = document.querySelector(".upload-section");

    const message = document.getElementById("message");
    const searchSection = document.querySelector(".search-section");

    let socket = null;
    let reconnectTimer = null;
    let reconnectDelay = 1000;
    let manuallyClosed = false;

    let selectedFiles = [];
    let isUploading = false;
    let isSearching = false;

    /* --------------------------------------------------
       Éléments d'interface
    -------------------------------------------------- */

    function showMessage(text, type = "info") {
        if (!message) return;

        message.textContent = text;
        message.dataset.type = type;
    }

    function createElement(tag, className, text = "") {
        const element = document.createElement(tag);

        if (className) {
            element.className = className;
        }

        element.textContent = text;

        return element;
    }

    function createActionButton(text, className, onClick) {
        const button = createElement("button", className, text);

        button.type = "button";
        button.addEventListener("click", onClick);

        return button;
    }

    /* --------------------------------------------------
       Client HTTP
    -------------------------------------------------- */

    async function apiRequest(path, options = {}) {
        const response = await fetch(path, {
            ...options,
            headers: {
                ...(options.body instanceof FormData
                    ? {}
                    : { "Content-Type": "application/json" }),
                ...options.headers,
            },
        });

        const contentType = response.headers.get("content-type") || "";

        let data;

        if (contentType.includes("application/json")) {
            data = await response.json();
        } else {
            data = await response.text();
        }

        if (!response.ok) {
            let detail = "";

            if (typeof data === "string") {
                detail = data;
            } else if (data && typeof data.detail === "string") {
                detail = data.detail;
            } else {
                detail = `Erreur HTTP ${response.status}`;
            }

            const error = new Error(detail);
            error.status = response.status;
            error.data = data;

            throw error;
        }

        return data;
    }

    function describeApiError(error, feature) {
        if (error.status === 501) {
            return `${feature} : la route existe, mais sa logique n'est pas encore implémentée côté serveur.`;
        }

        if (error.status === 422) {
            return `Requête invalide : ${error.message}`;
        }

        if (error.status === 404) {
            return "Ressource introuvable.";
        }

        if (error.status === 413) {
            return "Le fichier ou la requête est trop volumineux.";
        }

        if (error instanceof TypeError) {
            return "Impossible de joindre l'API. Vérifie que le serveur fonctionne.";
        }

        return error.message || "Une erreur inattendue est survenue.";
    }

    /* --------------------------------------------------
       Statut du système
    -------------------------------------------------- */

    function setConnectionStatus(status, label) {
        if (connectionStatus) {
            connectionStatus.dataset.status = status;
        }

        if (connectionLabel) {
            connectionLabel.textContent = label;
        }
    }

    async function checkSystemStatus() {
        try {
            const data = await apiRequest(`${API_BASE}/status`);

            if (data.api === "available") {
                // Le WebSocket conserve la responsabilité du point
                // de connexion affiché dans la barre supérieure.
                return data;
            }

            return data;
        } catch (error) {
            console.error("Erreur de statut API :", error);
            return null;
        }
    }

    /* --------------------------------------------------
       WebSocket
    -------------------------------------------------- */

    function connectWebSocket() {
        if (manuallyClosed) return;

        if (
            socket &&
            (
                socket.readyState === WebSocket.OPEN ||
                socket.readyState === WebSocket.CONNECTING
            )
        ) {
            return;
        }

        if (!navigator.onLine) {
            setConnectionStatus("disconnected", "Pas de réseau");
            scheduleReconnect();
            return;
        }

        const protocol =
            window.location.protocol === "https:" ? "wss:" : "ws:";

        const url = `${protocol}//${window.location.host}/ws/status`;

        setConnectionStatus("connecting", "Connexion...");

        try {
            socket = new WebSocket(url);
        } catch (error) {
            console.error("Impossible de créer le WebSocket :", error);

            setConnectionStatus("disconnected", "Hors ligne");
            scheduleReconnect();

            return;
        }

        socket.addEventListener("open", () => {
            reconnectDelay = 1000;
            setConnectionStatus("connected", "Système joignable");
            checkSystemStatus();
        });

        socket.addEventListener("message", (event) => {
            // Réservé aux futurs événements envoyés par le serveur.
            // On ne fait pas confiance aux données reçues sans validation.
            console.debug("Événement WebSocket reçu :", event.data);
        });

        socket.addEventListener("error", () => {
            setConnectionStatus("disconnected", "Connexion perdue");
        });

        socket.addEventListener("close", () => {
            if (!manuallyClosed) {
                setConnectionStatus("disconnected", "Hors ligne");
                scheduleReconnect();
            }
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

        if (reconnectTimer !== null) {
            window.clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }

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

    /* --------------------------------------------------
       Résultats de recherche
    -------------------------------------------------- */

    let resultsSection = document.getElementById("search-results");

    if (!resultsSection && searchSection) {
        resultsSection = createElement("section", "search-results");
        resultsSection.id = "search-results";
        resultsSection.setAttribute("aria-live", "polite");
        resultsSection.hidden = true;

        searchSection.insertAdjacentElement("afterend", resultsSection);
    }

    function clearResults() {
        if (!resultsSection) return;

        resultsSection.replaceChildren();
        resultsSection.hidden = true;
    }

    function renderSearchResults(data) {
        if (!resultsSection) return;

        resultsSection.replaceChildren();
        resultsSection.hidden = false;

        const results = Array.isArray(data.results) ? data.results : [];

        const heading = createElement(
            "h2",
            "results-heading",
            `Résultats (${Number.isFinite(data.total) ? data.total : results.length})`
        );

        resultsSection.append(heading);

        if (results.length === 0) {
            resultsSection.append(
                createElement(
                    "p",
                    "results-empty",
                    "Aucun résultat trouvé."
                )
            );

            return;
        }

        for (const result of results) {
            const card = createElement("article", "result-card");

            const title = createElement(
                "h3",
                "result-title",
                typeof result.title === "string"
                    ? result.title
                    : "Document sans titre"
            );

            const metadata = [];

            if (typeof result.language === "string") {
                metadata.push(result.language);
            }

            if (typeof result.source === "string") {
                metadata.push(result.source);
            }

            if (typeof result.score === "number") {
                metadata.push(`Score : ${result.score.toFixed(2)}`);
            }

            const details = createElement(
                "p",
                "result-meta",
                metadata.join(" · ")
            );

            const snippet = createElement(
                "p",
                "result-snippet",
                typeof result.snippet === "string" ? result.snippet : ""
            );

            card.append(title);

            if (metadata.length > 0) {
                card.append(details);
            }

            if (snippet.textContent) {
                card.append(snippet);
            }

            if (typeof result.document_id === "string") {
                card.dataset.documentId = result.document_id;
            }

            resultsSection.append(card);
        }

        if (typeof data.took_ms === "number") {
            resultsSection.append(
                createElement(
                    "p",
                    "results-time",
                    `Recherche effectuée en ${data.took_ms} ms`
                )
            );
        }
    }

    if (searchForm && searchInput) {
        searchForm.addEventListener("submit", async (event) => {
            event.preventDefault();

            if (isSearching) return;

            const query = searchInput.value.trim();

            if (!query) {
                searchInput.focus();
                showMessage("Entre une recherche.", "error");
                return;
            }

            isSearching = true;

            if (searchButton) {
                searchButton.disabled = true;
                searchButton.textContent = "Recherche...";
            }

            showMessage("Recherche en cours...");
            clearResults();

            try {
                const data = await apiRequest(`${API_BASE}/search`, {
                    method: "POST",
                    body: JSON.stringify({
                        query,
                        limit: 10,
                        language: null,
                    }),
                });

                renderSearchResults(data);
                showMessage("Recherche terminée.", "success");
            } catch (error) {
                console.error("Erreur de recherche :", error);

                showMessage(
                    describeApiError(error, "Recherche"),
                    "error"
                );
            } finally {
                isSearching = false;

                if (searchButton) {
                    searchButton.disabled = false;
                    searchButton.textContent = "Chercher";
                }
            }
        });
    }

    /* --------------------------------------------------
       Gestion des fichiers sélectionnés
    -------------------------------------------------- */

    function formatFileSize(bytes) {
        if (bytes < 1024) return `${bytes} o`;

        if (bytes < 1024 * 1024) {
            return `${(bytes / 1024).toFixed(1)} Ko`;
        }

        if (bytes < 1024 * 1024 * 1024) {
            return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
        }

        return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} Go`;
    }

    function getFileExtension(filename) {
        const index = filename.lastIndexOf(".");

        return index >= 0
            ? filename.slice(index + 1).toLowerCase()
            : "file";
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
                `${added} fichier${added > 1 ? "s ajoutés" : " ajouté"} à la sélection.`
            );
        }
    }

    function removeFile(index) {
        if (isUploading) return;

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
            const card = createElement("div", "file-card");

            const icon = createElement(
                "div",
                "file-icon",
                getFileExtension(file.name).slice(0, 5)
            );

            const info = createElement("div", "file-info");

            const name = createElement("span", "file-name", file.name);
            name.title = file.name;

            const size = createElement(
                "span",
                "file-size",
                formatFileSize(file.size)
            );

            const removeButton = createActionButton(
                "×",
                "file-remove",
                () => removeFile(index)
            );

            removeButton.title = "Retirer ce fichier";
            removeButton.setAttribute(
                "aria-label",
                `Retirer ${file.name}`
            );
            removeButton.disabled = isUploading;

            info.append(name, size);
            card.append(icon, info, removeButton);
            fileList.append(card);
        });

        updateUploadButton();
    }

    /* --------------------------------------------------
       Envoi des fichiers à l'API
    -------------------------------------------------- */

    let uploadButton = document.getElementById("upload-button");

    if (!uploadButton && uploadSection) {
        uploadButton = createActionButton(
            "Importer les fichiers",
            "upload-action",
            uploadSelectedFiles
        );

        uploadButton.id = "upload-button";
        uploadButton.hidden = true;

        uploadButton.style.marginTop = "16px";
        uploadButton.style.padding = "12px 18px";
        uploadButton.style.border = "0";
        uploadButton.style.borderRadius = "10px";
        uploadButton.style.background = "#8b7bff";
        uploadButton.style.color = "#ffffff";
        uploadButton.style.fontWeight = "650";

        uploadSection.append(uploadButton);
    }

    function updateUploadButton() {
        if (!uploadButton) return;

        uploadButton.hidden = selectedFiles.length === 0;
        uploadButton.disabled = isUploading;

        uploadButton.textContent = isUploading
            ? "Importation..."
            : `Importer ${selectedFiles.length} fichier${selectedFiles.length !== 1 ? "s" : ""}`;
    }

    async function uploadSelectedFiles() {
        if (isUploading || selectedFiles.length === 0) return;

        isUploading = true;
        renderFiles();
        showMessage("Envoi des fichiers en cours...");

        const formData = new FormData();

        for (const file of selectedFiles) {
            formData.append("files", file, file.name);
        }

        try {
            const data = await apiRequest(`${API_BASE}/documents`, {
                method: "POST",
                body: formData,
            });

            const importedCount =
                typeof data.imported === "number"
                    ? data.imported
                    : selectedFiles.length;

            selectedFiles = [];
            renderFiles();

            showMessage(
                `${importedCount} fichier${importedCount !== 1 ? "s importés" : " importé"} avec succès.`,
                "success"
            );

            await loadDocuments();
        } catch (error) {
            console.error("Erreur d'import :", error);

            showMessage(
                describeApiError(error, "Import des fichiers"),
                "error"
            );
        } finally {
            isUploading = false;
            renderFiles();
        }
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

    /* --------------------------------------------------
       Liste des documents déjà indexés
    -------------------------------------------------- */

    async function loadDocuments() {
        try {
            const data = await apiRequest(`${API_BASE}/documents?limit=50&offset=0`);

            // Cette liste pourra ensuite afficher les documents déjà
            // présents dans la base, indépendamment des fichiers locaux.
            console.debug("Documents récupérés :", data);

            return data;
        } catch (error) {
            if (error.status !== 501) {
                console.error("Impossible de récupérer les documents :", error);
            }

            return null;
        }
    }

    /* --------------------------------------------------
       Initialisation
    -------------------------------------------------- */

    checkSystemStatus();
});