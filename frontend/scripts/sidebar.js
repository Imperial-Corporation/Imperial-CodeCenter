// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Barre de gauche : un rail étroit qui se déplie quand la souris (ou le
 * clavier) s'en approche. Elle liste les discussions définitives du compte
 * connecté, et donne accès au compte.
 *
 * `ICC.sidebar.init({ onNew, onOpen, onDeleted, onRenamed })`
 *   → { refresh, setActive }
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function init(handlers) {
        const root = document.getElementById("sidebar");
        const list = document.getElementById("chat-list");
        const title = root.querySelector(".sb-title");
        const newButton = document.getElementById("new-chat");
        const accountButton = document.getElementById("account-button");
        const accountLabel = document.getElementById("account-label");
        const accountAvatar = document.getElementById("account-avatar");
        const toggle = document.getElementById("sidebar-toggle");

        const avatarIcon = accountAvatar.innerHTML;
        let activeId = null;
        let chats = [];
        let editing = null;

        /* Mobile : bouton qui ouvre le panneau. */
        function setOpen(open) {
            root.classList.toggle("is-open", open);
            toggle.setAttribute("aria-expanded", String(open));
        }
        toggle.addEventListener("click", () => setOpen(!root.classList.contains("is-open")));
        document.addEventListener("keydown", (event) => { if (event.key === "Escape") setOpen(false); });
        document.addEventListener("click", (event) => {
            if (root.classList.contains("is-open") && !root.contains(event.target) && !toggle.contains(event.target)) {
                setOpen(false);
            }
        });

        newButton.addEventListener("click", () => { setOpen(false); handlers.onNew(); });
        accountButton.addEventListener("click", () => ICC.auth.open("login"));

        function renderAccount(user) {
            if (user) {
                accountAvatar.textContent = user.username.slice(0, 1);
                accountLabel.textContent = user.username;
                accountButton.title = "Mon compte";
            } else {
                accountAvatar.innerHTML = avatarIcon;
                accountLabel.textContent = "Se connecter";
                accountButton.title = "Se connecter ou créer un compte";
            }
        }

        function emptyMessage(text) {
            const li = el("li", "sb-empty", text);
            list.replaceChildren(li);
        }

        async function startRename(chat, row) {
            editing = chat.id;
            const input = el("input", "sb-rename");
            input.value = chat.title;
            input.maxLength = 200;
            input.setAttribute("aria-label", "Nouveau titre");
            row.replaceChildren(input);
            input.focus();
            input.select();

            let done = false;
            const finish = async (save) => {
                if (done) return;
                done = true;
                editing = null;
                const value = input.value.trim();
                if (save && value && value !== chat.title) {
                    try {
                        await ICC.services.renameChat(chat.id, value);
                        chat.title = value;
                        if (handlers.onRenamed) handlers.onRenamed(chat.id, value);
                    } catch (error) {
                        console.error("Renommage impossible :", error);
                    }
                }
                render();
            };
            input.addEventListener("keydown", (event) => {
                if (event.key === "Enter") finish(true);
                if (event.key === "Escape") finish(false);
            });
            input.addEventListener("blur", () => finish(true));
        }

        async function remove(chat) {
            if (!window.confirm(ICC.i18n ? ICC.i18n.t(`Supprimer définitivement « ${chat.title} » ?`) : `Supprimer définitivement « ${chat.title} » ?`)) return;
            try {
                await ICC.services.deleteChat(chat.id);
            } catch (error) {
                console.error("Suppression impossible :", error);
                return;
            }
            chats = chats.filter((c) => c.id !== chat.id);
            render();
            if (handlers.onDeleted) handlers.onDeleted(chat.id);
        }

        function render() {
            const user = ICC.auth.user();
            title.hidden = !user;
            list.hidden = !user;
            if (!user) { list.replaceChildren(); return; }
            if (chats.length === 0) {
                emptyMessage("Aucune discussion enregistrée. Utilise « Enregistrer » dans une discussion pour la garder.");
                return;
            }
            list.replaceChildren(...chats.map((chat) => {
                const row = el("li", "sb-chat");
                if (chat.id === activeId) row.classList.add("is-active");
                const open = el("button", "sb-chat-open", chat.title);
                open.type = "button";
                open.title = chat.title;
                if (chat.id === activeId) open.setAttribute("aria-current", "true");
                open.addEventListener("click", () => { setOpen(false); handlers.onOpen(chat.id); });

                const rename = el("button", "sb-chat-tool", "✎");
                rename.type = "button";
                rename.title = "Renommer";
                rename.setAttribute("aria-label", `Renommer ${chat.title}`);
                rename.addEventListener("click", () => startRename(chat, row));

                const del = el("button", "sb-chat-tool danger", "✕");
                del.type = "button";
                del.title = "Supprimer";
                del.setAttribute("aria-label", `Supprimer ${chat.title}`);
                del.addEventListener("click", () => remove(chat));

                row.append(open, rename, del);
                return row;
            }));
        }

        async function refresh() {
            if (!ICC.auth.user()) { chats = []; render(); return; }
            try {
                const data = await ICC.services.chats();
                chats = data.chats || [];
            } catch (error) {
                if (error.status === 401) ICC.auth.expire();
                chats = [];
            }
            if (!editing) render();
        }

        function setActive(id) {
            activeId = id;
            if (!editing) render();
        }

        ICC.auth.onChange((user) => {
            renderAccount(user);
            activeId = null;
            refresh();
        });
        renderAccount(ICC.auth.user());
        render();

        return { refresh, setActive };
    }

    ICC.sidebar = { init };
})();
