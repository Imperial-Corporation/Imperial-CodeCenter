"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const API_BASE = "/api/v1";
    const ICC = window.ICC;

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
       Recherche assistée par IA
       (appels réseau : api.js ; affichage : answer-view.js)
    -------------------------------------------------- */

    let resultsSection = document.getElementById("search-results");

    if (!resultsSection && searchSection) {
        resultsSection = createElement("section", "search-results");
        resultsSection.id = "search-results";
        resultsSection.setAttribute("aria-live", "polite");
        resultsSection.hidden = true;

        searchSection.insertAdjacentElement("afterend", resultsSection);
    }

    let searchController = null;

    function clearResults() {
        if (!resultsSection) return;

        resultsSection.replaceChildren();
        resultsSection.hidden = true;
    }

    function showInResults(...nodes) {
        resultsSection.replaceChildren(...nodes);
        resultsSection.hidden = false;
    }

    function newSearchButton() {
        return createActionButton("Nouvelle recherche", "ai-new-search", () => {
            clearResults();
            showMessage("");
            searchInput.focus();
            searchInput.select();
        });
    }

    function renderLoading() {
        const box = createElement("div", "ai-loading");
        box.setAttribute("role", "status");

        box.append(
            createElement("span", "ai-spinner"),
            createElement("span", "ai-loading-text", "Recherche dans l'index et analyse par l'IA…"),
            createActionButton("Annuler", "ai-cancel", () => {
                if (searchController) searchController.abort();
            }),
        );

        showInResults(box);
    }

    function renderFailure(error) {
        const box = createElement("div", "ai-error");
        box.setAttribute("role", "alert");

        box.append(createElement("strong", "", "La recherche a échoué"));
        box.append(createElement("p", "", error.message || "Erreur inattendue."));

        const code = error.code || "";

        if (code.startsWith("provider_auth") || code.startsWith("provider_not_found") || code === "config") {
            box.append(createActionButton("Ouvrir la configuration IA", "ai-cancel", () => {
                const fab = document.querySelector(".settings-fab");
                if (fab) fab.click();
            }));
        }

        showInResults(box, newSearchButton());
    }

    function renderResponse(data) {
        const conversationId = data.meta && data.meta.conversation_id;

        if (!conversationId || !ICC.chat) {
            showInResults(ICC.answerView.render(data), newSearchButton());
            return;
        }

        // La recherche ouvre une conversation : l'utilisateur peut poursuivre avec l'IA.
        const chat = ICC.chat.create({ conversationId });
        const view = ICC.answerView.render(data, {
            idPrefix: "first-",
            onFollowUp: (text) => chat.ask(text, "auto"),
        });
        showInResults(view, chat.element, newSearchButton());
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
            searchController = new AbortController();

            if (searchButton) {
                searchButton.disabled = true;
                searchButton.textContent = "Recherche...";
            }

            showMessage("");
            renderLoading();

            try {
                const data = await ICC.services.search(query, {
                    limit: 10,
                    signal: searchController.signal,
                });

                renderResponse(data);
            } catch (error) {
                if (error.name === "AbortError") {
                    clearResults();
                    showMessage("Recherche annulée.");
                } else {
                    console.error("Erreur de recherche :", error);
                    renderFailure(error);
                }
            } finally {
                isSearching = false;
                searchController = null;

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
       Import dans la bibliothèque (fichiers, ZIP, tags)
    -------------------------------------------------- */

    const importOptions = document.getElementById("import-options");
    const tagsInput = document.getElementById("tags-input");
    const tagPreview = document.getElementById("tag-preview");
    const autoClassify = document.getElementById("auto-classify");
    const keepFolders = document.getElementById("keep-folders");
    const importReport = document.getElementById("import-report");
    const importAdmin = document.getElementById("import-admin");
    const importAdminInput = document.getElementById("import-admin-input");
    const depotPath = document.getElementById("depot-path");
    const depotScan = document.getElementById("depot-scan");

    let uploadButton = document.getElementById("upload-button");

    if (!uploadButton && uploadSection) {
        uploadButton = createActionButton(
            "Importer les fichiers",
            "upload-action",
            uploadSelectedFiles
        );

        uploadButton.id = "upload-button";
        uploadButton.hidden = true;
        // Placé juste après les options d'import.
        (importOptions || uploadSection).insertAdjacentElement(
            importOptions ? "afterend" : "beforeend", uploadButton);
    }

    // Même règle que le serveur : minuscules, caractères sûrs, 8 tags au plus.
    function normalizeTags(text) {
        const tags = [];
        text.split(/[,;\n]+/).forEach((raw) => {
            const tag = raw.replace(/^#/, "").trim().toLowerCase()
                .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
                .replace(/[^a-z0-9._+-]+/g, "-").replace(/^[-._]+|[-._]+$/g, "").slice(0, 40);
            if (tag && !tags.includes(tag)) tags.push(tag);
        });
        return tags.slice(0, 8);
    }

    function renderTagPreview() {
        if (!tagPreview || !tagsInput) return;
        const tags = normalizeTags(tagsInput.value);
        tagPreview.replaceChildren();
        tags.forEach((tag) => tagPreview.append(createElement("span", "tag-chip", tag)));
        if (tags.length > 1) {
            tagPreview.append(createElement("span", "tag-path", `→ ${tags.join(" / ")} / <langage>`));
        }
    }

    if (tagsInput) tagsInput.addEventListener("input", renderTagPreview);

    function updateUploadButton() {
        if (importOptions) importOptions.hidden = selectedFiles.length === 0;
        if (!uploadButton) return;

        uploadButton.hidden = selectedFiles.length === 0;
        uploadButton.disabled = isUploading;

        uploadButton.textContent = isUploading
            ? "Importation..."
            : `Importer ${selectedFiles.length} fichier${selectedFiles.length !== 1 ? "s" : ""}`;
    }

    function renderImportReport(report) {
        if (!importReport) return;
        importReport.replaceChildren();

        const saved = report.saved || [];
        const skipped = report.skipped || [];

        const summary = createElement("p", "import-summary",
            `${saved.length} script${saved.length > 1 ? "s" : ""} rangé${saved.length > 1 ? "s" : ""}` +
            (skipped.length ? `, ${skipped.length} ignoré${skipped.length > 1 ? "s" : ""}` : ""));
        summary.dataset.kind = saved.length ? "ok" : "error";
        importReport.append(summary);

        if (saved.length) {
            const list = createElement("ul", "import-list");
            saved.slice(0, 50).forEach((item) => {
                list.append(createElement("li", "", `${item.path} (${item.language})`));
            });
            if (saved.length > 50) list.append(createElement("li", "", `… et ${saved.length - 50} autres`));
            importReport.append(list);
        }

        if (skipped.length) {
            const details = createElement("details", "import-skipped");
            details.open = saved.length === 0;
            details.append(createElement("summary", "", "Fichiers ignorés"));
            const list = createElement("ul", "import-list");
            skipped.slice(0, 100).forEach((item) => {
                list.append(createElement("li", "", `${item.name} : ${item.reason}`));
            });
            details.append(list);
            importReport.append(details);
        }

        importReport.hidden = false;
    }

    async function uploadSelectedFiles() {
        if (isUploading || selectedFiles.length === 0) return;

        isUploading = true;
        renderFiles();
        showMessage("Envoi des fichiers en cours...");

        if (importAdminInput && importAdminInput.value.trim()) {
            ICC.services.adminToken.set(importAdminInput.value.trim());
        }

        try {
            const report = await ICC.services.libraryImport(
                selectedFiles,
                tagsInput ? tagsInput.value : "",
                {
                    autoClassify: autoClassify ? autoClassify.checked : true,
                    keepFolders: keepFolders ? keepFolders.checked : true,
                },
            );

            const keep = selectedFiles.filter((file) =>
                (report.skipped || []).some((item) => item.name === file.name));

            selectedFiles = keep;
            if (keep.length === 0 && tagsInput) {
                tagsInput.value = "";
                renderTagPreview();
            }
            renderFiles();
            renderImportReport(report);
            if (importAdmin) importAdmin.hidden = true;

            showMessage(
                report.saved_count > 0
                    ? "Scripts rangés : ils seront disponibles dans la recherche dans quelques secondes."
                    : "Aucun script n'a pu être rangé.",
                report.saved_count > 0 ? "success" : "error"
            );
        } catch (error) {
            console.error("Erreur d'import :", error);

            if (error.status === 401 && importAdmin) {
                importAdmin.hidden = false;
                showMessage("Jeton d'administration requis ou invalide.", "error");
            } else {
                showMessage(describeApiError(error, "Import des fichiers"), "error");
            }
        } finally {
            isUploading = false;
            renderFiles();
        }
    }

    if (depotScan) {
        depotScan.addEventListener("click", async () => {
            depotScan.disabled = true;
            try {
                const report = await ICC.services.libraryScanDepot();
                renderImportReport(report);
                showMessage(`${report.saved_count} script(s) rangé(s) depuis la zone de dépôt.`, "success");
            } catch (error) {
                if (error.status === 401 && importAdmin) importAdmin.hidden = false;
                showMessage(describeApiError(error, "Zone de dépôt"), "error");
            } finally {
                depotScan.disabled = false;
            }
        });
    }

    if (depotPath) {
        ICC.services.libraryInfo()
            .then((info) => { depotPath.textContent = info.depot; })
            .catch(() => { /* jeton requis ou serveur sans bibliothèque : le chemin reste générique */ });
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
       Initialisation
    -------------------------------------------------- */

    checkSystemStatus();
});