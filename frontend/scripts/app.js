// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";


document.addEventListener("DOMContentLoaded", () => {
    const API_BASE = "/api/v1";
    const ICC = window.ICC;

    const connectionStatus = document.getElementById("connection-status");
    const connectionLabel = document.getElementById("connection-label");

    const form = document.getElementById("search-form");
    const input = document.getElementById("search-input");
    const sendButton = document.getElementById("search-button");
    const cancelButton = document.getElementById("cancel-button");
    const deepToggle = document.getElementById("deep-toggle");
    const message = document.getElementById("message");
    const thread = document.getElementById("thread");
    const chatTitle = document.getElementById("chat-title");
    const chatState = document.getElementById("chat-state");
    const saveButton = document.getElementById("save-chat");

    let socket = null;
    let reconnectTimer = null;
    let reconnectDelay = 1000;
    let manuallyClosed = false;

    function showMessage(text, type = "info") {
        if (!message) return;
        message.textContent = text;
        message.dataset.type = type;
    }

    /* --------------------------------------------------
       Client HTTP minimal (statut du système)
    -------------------------------------------------- */

    async function apiRequest(path) {
        const response = await fetch(path);
        if (!response.ok) throw new Error(`Erreur HTTP ${response.status}`);
        return response.json();
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
       Discussion
    -------------------------------------------------- */

    let sidebar = null;

    function autoGrow() {
        input.style.height = "auto";
        input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
    }

    function refreshHeader(state) {
        const inChat = state.turns > 0 || state.busy || thread.childElementCount > 0;
        document.body.dataset.mode = inChat ? "chat" : "home";

        chatTitle.textContent = state.title || "Nouvelle discussion";
        const saved = Boolean(state.savedId);
        chatState.dataset.saved = String(saved);
        chatState.textContent = saved ? "Enregistrée" : "Temporaire";
        saveButton.hidden = saved;
        saveButton.disabled = state.busy || !state.id;

        sendButton.disabled = state.busy;
        sendButton.textContent = state.busy ? "En cours…" : (inChat ? "Envoyer" : "Chercher");
        cancelButton.hidden = !state.busy;
        input.placeholder = inChat
            ? "Pose une question de suite…"
            : "Recherche du code, une fonction, une idée...";
        if (sidebar) sidebar.setActive(state.savedId);
    }

    const conversation = ICC.conversation.create({ thread, onChange: refreshHeader });

    async function submit() {
        const text = input.value.trim();
        if (!text) {
            input.focus();
            showMessage("Entre une question.", "error");
            return;
        }
        if (conversation.state().busy) return;
        showMessage("");
        input.value = "";
        autoGrow();
        const ok = await conversation.ask(text, { depth: deepToggle.checked ? "deep" : "auto" });
        if (ok) deepToggle.checked = false;
        else if (!input.value) { input.value = text; autoGrow(); }
        input.focus();
    }

    form.addEventListener("submit", (event) => { event.preventDefault(); submit(); });
    input.addEventListener("input", autoGrow);
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            submit();
        }
    });
    cancelButton.addEventListener("click", () => conversation.abort());

    function startNew() {
        conversation.reset();
        showMessage("");
        input.focus();
    }

    async function openSaved(id) {
        if (conversation.state().busy) conversation.abort();
        showMessage("");
        try {
            const opened = await ICC.services.openChat(id);
            conversation.load(opened);
            input.focus();
        } catch (error) {
            if (error.status === 401) ICC.auth.expire();
            showMessage(error.message || "Impossible d'ouvrir cette discussion.", "error");
        }
    }

    saveButton.addEventListener("click", async () => {
        const state = conversation.state();
        if (!state.id || state.savedId) return;
        if (!ICC.auth.user()) {
            showMessage("Connecte-toi pour enregistrer cette discussion.");
            ICC.auth.open("login");
            return;
        }
        saveButton.disabled = true;
        try {
            const saved = await ICC.services.saveChat(state.id, state.title);
            conversation.markSaved(saved.id, saved.title);
            await sidebar.refresh();
            sidebar.setActive(saved.id);
            showMessage("Discussion enregistrée : la suite s'y ajoutera automatiquement.", "success");
        } catch (error) {
            if (error.status === 401) ICC.auth.expire();
            showMessage(error.message || "Enregistrement impossible.", "error");
            saveButton.disabled = false;
        }
    });

    sidebar = ICC.sidebar.init({
        onNew: startNew,
        onOpen: openSaved,
        onDeleted: (id) => { if (conversation.state().savedId === id) startNew(); },
        onRenamed: (id, title) => { if (conversation.state().savedId === id) conversation.markSaved(id, title); },
    });

    /* --------------------------------------------------
       Pied de page : origine et licence (identité du projet)
    -------------------------------------------------- */

    ICC.services.about().then((about) => {
        const version = document.getElementById("foot-version");
        const license = document.getElementById("foot-license");
        if (version) version.textContent = `v${about.version}`;
        if (license && about.license) license.textContent = about.license.name;
        document.title = `${about.name} — ${about.organization}`;
    }).catch(() => { /* le pied de page garde son texte par défaut */ });

    /* --------------------------------------------------
       Initialisation
    -------------------------------------------------- */

    refreshHeader(conversation.state());
    ICC.auth.init();
    checkSystemStatus();
    input.focus();
});
