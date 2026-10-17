// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Couche d'accès à l'API (services). Aucune manipulation du DOM ici :
 * l'interface et la future chatbox appellent ces fonctions.
 */
(function () {
    const API_BASE = "/api/v1";
    const TOKEN_KEY = "icc-admin-token";

    const ICC = (window.ICC = window.ICC || {});

    /* Jeton d'administration facultatif (CODECENTER_ADMIN_TOKEN côté serveur).
       Gardé pour l'onglet seulement ; le stockage peut être indisponible. */
    const adminToken = {
        get() {
            try {
                return window.sessionStorage.getItem(TOKEN_KEY) || "";
            } catch {
                return "";
            }
        },
        set(value) {
            try {
                if (value) window.sessionStorage.setItem(TOKEN_KEY, value);
                else window.sessionStorage.removeItem(TOKEN_KEY);
            } catch {
                /* stockage indisponible : le jeton ne sera pas conservé */
            }
        },
    };

    class ApiError extends Error {
        constructor(message, status, code) {
            super(message);
            this.status = status;
            this.code = code || null;
        }
    }

    async function http(path, { method = "GET", body, form, signal, admin = false } = {}) {
        const headers = {};

        // FormData : le navigateur fixe lui-même le Content-Type (avec la frontière).
        if (body !== undefined && !form) headers["Content-Type"] = "application/json";

        const token = admin ? adminToken.get() : "";
        if (token) headers.Authorization = `Bearer ${token}`;

        let response;

        try {
            response = await fetch(`${API_BASE}${path}`, {
                method,
                headers,
                body: form ? form : (body === undefined ? undefined : JSON.stringify(body)),
                signal,
            });
        } catch (error) {
            if (error.name === "AbortError") throw error;
            throw new ApiError("Impossible de joindre le serveur.", 0, "network");
        }

        let data = null;

        try {
            data = await response.json();
        } catch {
            data = null;
        }

        if (!response.ok) {
            throw new ApiError(describeError(data, response.status), response.status, data && data.code);
        }

        return data;
    }

    function describeError(data, status) {
        if (data && typeof data.detail === "string") return data.detail;

        // Erreurs de validation FastAPI : liste d'objets {loc, msg}.
        if (data && Array.isArray(data.detail) && data.detail.length > 0) {
            const first = data.detail[0];
            const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : "";
            return `Valeur invalide${field ? ` (${field})` : ""} : ${first.msg || "format incorrect"}.`;
        }

        return `Erreur du serveur (HTTP ${status}).`;
    }

    ICC.services = {
        adminToken,
        ApiError,

        search(query, options = {}) {
            return http("/search", {
                method: "POST",
                body: {
                    query,
                    limit: options.limit || 10,
                    language: options.language || null,
                    category: options.category || null,
                    depth: options.depth || "auto",
                },
                signal: options.signal,
            });
        },

        /* Message suivant d'une conversation ouverte par une recherche. */
        chat(conversationId, message, options = {}) {
            return http("/chat", {
                method: "POST",
                body: {
                    conversation_id: conversationId,
                    message,
                    depth: options.depth || "auto",
                    language: options.language || null,
                    category: options.category || null,
                },
                signal: options.signal,
            });
        },

        /* Identité du projet (version, origine, licence). */
        about: () => http("/about"),
        indexHealth: () => http("/index/health", { admin: true }),

        /* Comptes (cookie de session HttpOnly : rien à stocker côté navigateur). */
        me: () => http("/auth/me"),
        register: (username, password) => http("/auth/register", { method: "POST", body: { username, password } }),
        login: (username, password) => http("/auth/login", { method: "POST", body: { username, password } }),
        logout: () => http("/auth/logout", { method: "POST" }),
        changePassword: (current, next) => http("/auth/password", { method: "POST", body: { current, new: next } }),
        deleteAccount: (password) => http("/auth/delete", { method: "POST", body: { password } }),

        /* Discussions enregistrées. */
        chats: () => http("/chats"),
        saveChat: (conversationId, title) =>
            http("/chats", { method: "POST", body: { conversation_id: conversationId, title: title || null } }),
        openChat: (id) => http(`/chats/${encodeURIComponent(id)}/open`, { method: "POST" }),
        renameChat: (id, title) => http(`/chats/${encodeURIComponent(id)}`, { method: "PATCH", body: { title } }),
        deleteChat: (id) => http(`/chats/${encodeURIComponent(id)}`, { method: "DELETE" }),

        /* Crawler GitHub. */
        crawlerStatus: () => http("/crawler", { admin: true }),
        crawlerStart: (request) => http("/crawler/start", { method: "POST", body: request, admin: true }),
        crawlerStop: () => http("/crawler/stop", { method: "POST", admin: true }),
        crawlerToken: (token, clear) =>
            http("/crawler/token", { method: "PUT", body: { token: token || null, clear: !!clear }, admin: true }),

        aiCatalog: () => http("/ai/catalog"),
        aiConfig: () => http("/ai/config", { admin: true }),

        aiTest: (providerId, config) =>
            http(`/ai/providers/${encodeURIComponent(providerId)}/test`, {
                method: "POST", body: config, admin: true,
            }),

        aiSave: (providerId, config) =>
            http(`/ai/providers/${encodeURIComponent(providerId)}`, {
                method: "PUT", body: config, admin: true,
            }),
    };
})();
