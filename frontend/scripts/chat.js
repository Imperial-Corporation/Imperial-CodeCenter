"use strict";

/*
 * Chatbox qui prolonge une recherche : l'utilisateur continue la discussion
 * avec l'IA, qui garde les sources déjà trouvées (mêmes identifiants S1, S2…)
 * et peut toujours utiliser ses outils (recherche, lecture de fichier…).
 *
 * `ICC.chat.create({ conversationId })` retourne { element, ask }.
 * Chaque réponse est affichée par ICC.answerView (Markdown sûr).
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});
    let counter = 0;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function create({ conversationId }) {
        const services = ICC.services;
        const prefix = `c${++counter}-`;
        let turn = 0;
        let pending = null; // AbortController de la requête en cours
        let conversation = conversationId;
        let closed = false;

        const root = el("section", "ai-chat");
        root.setAttribute("aria-label", "Discussion avec l'IA");
        root.append(el("h3", "ai-h", "Continuer la discussion"));

        const thread = el("div", "ai-chat-thread");
        thread.setAttribute("aria-live", "polite");

        const form = el("form", "ai-chat-form");
        const input = el("textarea", "ai-chat-input");
        input.rows = 2;
        input.maxLength = 2000;
        input.placeholder = "Pose une question de suite, demande un exemple, un autre langage…";
        input.setAttribute("aria-label", "Message pour l'IA");

        const row = el("div", "ai-chat-row");
        const deepLabel = el("label", "ai-chat-deep");
        const deep = el("input");
        deep.type = "checkbox";
        deepLabel.append(deep, document.createTextNode(" Approfondir (cherche des sources spécialisées)"));

        const deepenNow = el("button", "ai-chat-secondary", "Approfondir la réponse");
        deepenNow.type = "button";

        const send = el("button", "ai-chat-send", "Envoyer");
        send.type = "submit";
        const cancel = el("button", "ai-chat-secondary", "Annuler");
        cancel.type = "button";
        cancel.hidden = true;

        row.append(deepLabel, deepenNow, cancel, send);
        const note = el("p", "ai-chat-note");
        note.setAttribute("role", "status");
        form.append(input, row, note);
        root.append(thread, form);

        function setBusy(busy) {
            send.disabled = busy || closed;
            deepenNow.disabled = busy || closed;
            input.disabled = closed;
            cancel.hidden = !busy;
        }

        function scrollToEnd(node) {
            if (node && node.scrollIntoView) node.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }

        function bubble(text) {
            const node = el("div", "ai-chat-user");
            node.append(el("span", "ai-chat-who", "Toi"), el("p", "", text));
            return node;
        }

        function failure(error) {
            const box = el("div", "ai-error");
            box.setAttribute("role", "alert");
            box.append(el("p", "", error.message || "Erreur inattendue."));
            return box;
        }

        async function ask(text, depth) {
            const message = String(text || "").trim();
            if (!message || pending || closed) return;

            turn += 1;
            const answerPrefix = `${prefix}${turn}-`;
            thread.append(bubble(message));

            const loading = el("div", "ai-loading");
            loading.setAttribute("role", "status");
            loading.append(el("span", "ai-spinner"), el("span", "ai-loading-text", "L'IA réfléchit…"));
            thread.append(loading);
            scrollToEnd(loading);

            pending = new AbortController();
            setBusy(true);
            note.textContent = "";

            try {
                const data = await services.chat(conversation, message, {
                    depth: depth || (deep.checked ? "deep" : "auto"),
                    signal: pending.signal,
                });
                conversation = (data.meta && data.meta.conversation_id) || conversation;
                const view = ICC.answerView.render(data, {
                    idPrefix: answerPrefix,
                    onFollowUp: (followUp) => ask(followUp, "auto"),
                });
                view.classList.add("ai-chat-answer");
                loading.replaceWith(view);
                scrollToEnd(view);
                input.value = "";
                deep.checked = false;
            } catch (error) {
                if (error.name === "AbortError") {
                    loading.remove();
                    note.textContent = "Message annulé.";
                } else {
                    loading.replaceWith(failure(error));
                    if (error.code === "chat_not_found" || error.code === "chat_too_long") {
                        closed = true;
                        note.textContent = "Cette conversation est terminée : lance une nouvelle recherche pour continuer.";
                    }
                }
            } finally {
                pending = null;
                setBusy(false);
                if (!closed) input.focus();
            }
        }

        form.addEventListener("submit", (event) => {
            event.preventDefault();
            ask(input.value);
        });

        input.addEventListener("keydown", (event) => {
            // Entrée envoie, Maj+Entrée ajoute une ligne.
            if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                ask(input.value);
            }
        });

        deepenNow.addEventListener("click", () =>
            ask("Approfondis ta réponse précédente : détaille et cherche des sources plus spécialisées.", "deep"));
        cancel.addEventListener("click", () => { if (pending) pending.abort(); });

        return { element: root, ask: (text, depth) => ask(text, depth), abort: () => pending && pending.abort() };
    }

    ICC.chat = { create };
})();
