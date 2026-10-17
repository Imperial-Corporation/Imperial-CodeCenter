// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Discussion avec l'IA : la première question lance la recherche, les
 * suivantes poursuivent la même conversation (mêmes sources S1, S2…, mêmes
 * outils). Chaque échange est un bloc « question + réponse ».
 *
 * Une discussion est temporaire (en mémoire du serveur) tant qu'elle n'est pas
 * enregistrée par un compte ; une discussion enregistrée se rouvre et se
 * poursuit, même après un redémarrage du serveur.
 *
 * `ICC.conversation.create({ thread, onChange })` retourne
 * { ask, load, reset, abort, state }.
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

    const DEEPEN_TEXT = "Approfondis ta réponse précédente : détaille et cherche des sources plus spécialisées.";

    function create({ thread, onChange }) {
        const state = { id: null, savedId: null, title: "", busy: false, turns: 0 };
        let pending = null;

        const notify = () => onChange && onChange({ ...state });

        function userBubble(text) {
            const box = el("div", "turn-user");
            box.append(el("p", "", text));
            return box;
        }

        function loadingBox(first) {
            const box = el("div", "ai-loading");
            box.setAttribute("role", "status");
            box.append(
                el("span", "ai-spinner"),
                el("span", "ai-loading-text", first ? "Recherche dans l'index et analyse par l'IA…" : "L'IA réfléchit…"),
            );
            return box;
        }

        function failureBox(error, retry) {
            const box = el("div", "ai-error");
            box.setAttribute("role", "alert");
            box.append(el("strong", "", "La réponse a échoué"), el("p", "", error.message || "Erreur inattendue."));

            const code = error.code || "";
            const row = el("div", "ai-error-actions");
            if (retry) {
                const again = el("button", "ai-action", "Réessayer");
                again.type = "button";
                again.addEventListener("click", retry);
                row.append(again);
            }
            if (code.startsWith("provider_auth") || code.startsWith("provider_not_found") || code === "config") {
                const open = el("button", "ai-action", "Ouvrir la configuration IA");
                open.type = "button";
                open.addEventListener("click", () => {
                    const fab = document.querySelector(".settings-fab:not(.crawler-fab)");
                    if (fab) fab.click();
                });
                row.append(open);
            }
            if (row.childElementCount) box.append(row);
            return box;
        }

        function answerNode(data, turnId, allowActions) {
            return ICC.answerView.render(data, {
                idPrefix: `t${turnId}-`,
                onFollowUp: allowActions ? (text) => ask(text, { depth: "auto" }) : undefined,
                onDeepen: allowActions ? () => ask(DEEPEN_TEXT, { depth: "deep" }) : undefined,
            });
        }

        /* Rouvre côté serveur une discussion enregistrée dont la mémoire a expiré. */
        async function reopenSaved() {
            const opened = await ICC.services.openChat(state.savedId);
            state.id = opened.conversation_id;
        }

        async function request(text, depth, signal) {
            if (!state.id) {
                return ICC.services.search(text, { depth, signal });
            }
            try {
                return await ICC.services.chat(state.id, text, { depth, signal });
            } catch (error) {
                if (error.code === "chat_not_found" && state.savedId) {
                    await reopenSaved();
                    return ICC.services.chat(state.id, text, { depth, signal });
                }
                throw error;
            }
        }

        async function ask(text, options = {}) {
            const message = String(text || "").trim();
            if (!message || state.busy) return false;

            const first = !state.id && state.turns === 0;
            counter += 1;
            const turnId = counter;
            const turn = el("section", "turn");
            turn.dataset.turn = String(turnId);
            const bubble = userBubble(message);
            const loading = loadingBox(first);
            turn.append(bubble, loading);
            thread.append(turn);
            if (first) state.title = message.length > 80 ? `${message.slice(0, 79)}…` : message;

            pending = new AbortController();
            state.busy = true;
            notify();
            window.requestAnimationFrame(() => turn.scrollIntoView({ block: "start", behavior: "smooth" }));

            let ok = false;
            try {
                const data = await request(message, options.depth || "auto", pending.signal);
                const conversationId = data.meta && data.meta.conversation_id;
                if (conversationId) state.id = conversationId;
                state.turns += 1;
                const continuable = Boolean(conversationId);
                loading.replaceWith(answerNode(data, turnId, continuable));
                ok = true;
            } catch (error) {
                if (error.name === "AbortError") {
                    turn.remove();
                    if (first) state.title = "";
                } else {
                    console.error("Erreur de discussion :", error);
                    const retry = () => { turn.remove(); ask(message, options); };
                    const lost = error.code === "chat_not_found";
                    if (lost) {
                        error.message = "Cette discussion n'est plus en mémoire (le serveur a redémarré ou elle a expiré). " +
                            "Pose ta question dans une nouvelle discussion.";
                    }
                    loading.replaceWith(failureBox(error, lost ? null : retry));
                }
            } finally {
                pending = null;
                state.busy = false;
                notify();
            }
            return ok;
        }

        function reset() {
            if (pending) pending.abort();
            thread.replaceChildren();
            Object.assign(state, { id: null, savedId: null, title: "", busy: false, turns: 0 });
            notify();
        }

        /* Affiche une discussion enregistrée (réponse de POST /chats/{id}/open). */
        function load(opened) {
            if (pending) pending.abort();
            thread.replaceChildren();
            Object.assign(state, {
                id: opened.conversation_id, savedId: opened.id, title: opened.title, busy: false,
                turns: opened.turns.length,
            });
            opened.turns.forEach((entry, index) => {
                counter += 1;
                const turn = el("section", "turn");
                const last = index === opened.turns.length - 1;
                turn.append(userBubble(entry.question), answerNode(entry.response, counter, last));
                thread.append(turn);
            });
            notify();
            const lastTurn = thread.lastElementChild;
            if (lastTurn) window.requestAnimationFrame(() => lastTurn.scrollIntoView({ block: "start" }));
        }

        function markSaved(chatId, title) {
            state.savedId = chatId;
            if (title) state.title = title;
            notify();
        }

        return {
            ask,
            load,
            reset,
            markSaved,
            abort: () => { if (pending) pending.abort(); },
            state: () => ({ ...state }),
        };
    }

    ICC.conversation = { create };
})();
