// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Compte : connexion, inscription, mot de passe, suppression.
 * La session vit dans un cookie HttpOnly posé par le serveur : ce script ne
 * voit jamais le jeton et ne stocke rien dans le navigateur.
 *
 * `ICC.auth` : { init, user, open, onChange }
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});

    let currentUser = null;
    let registrationOpen = true;
    let enabled = true;
    const listeners = [];
    let dialog = null;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function emit() {
        listeners.forEach((fn) => fn(currentUser));
    }

    function setUser(user) {
        currentUser = user || null;
        emit();
    }

    async function init() {
        try {
            const data = await ICC.services.me();
            registrationOpen = data.registration_open !== false;
            enabled = data.enabled !== false;
            setUser(data.user || null);
        } catch {
            setUser(null);
        }
        return currentUser;
    }

    function field(label, type, name, autocomplete) {
        const wrap = el("label", "auth-field");
        wrap.append(el("span", "", label));
        const input = el("input");
        input.type = type;
        input.name = name;
        input.autocomplete = autocomplete;
        input.required = true;
        if (type === "text") {
            input.spellcheck = false;
            input.autocapitalize = "none";
        }
        wrap.append(input);
        return { wrap, input };
    }

    function ensureDialog() {
        if (dialog) return dialog;
        dialog = el("dialog", "auth-dialog");
        dialog.setAttribute("aria-label", "Compte");
        dialog.addEventListener("click", (event) => {
            if (event.target === dialog) dialog.close();
        });
        document.body.append(dialog);
        return dialog;
    }

    function guard(button, errorBox, task) {
        return async (event) => {
            event.preventDefault();
            errorBox.textContent = "";
            button.disabled = true;
            try {
                await task();
            } catch (error) {
                errorBox.textContent = error.message || "Erreur inattendue.";
            } finally {
                button.disabled = false;
            }
        };
    }

    function renderSignedOut(mode) {
        const form = el("form", "auth-form");
        form.noValidate = false;
        const registering = mode === "register";

        const tabs = el("div", "auth-tabs");
        tabs.setAttribute("role", "tablist");
        [["login", "Connexion"], ["register", "Créer un compte"]].forEach(([id, label]) => {
            const tab = el("button", "auth-tab", label);
            tab.type = "button";
            tab.setAttribute("role", "tab");
            tab.setAttribute("aria-selected", String(id === mode));
            tab.addEventListener("click", () => render(id));
            tabs.append(tab);
        });

        const intro = el("p", "", registering
            ? "Un pseudo et un mot de passe suffisent. Tes discussions enregistrées te suivent d'un appareil à l'autre."
            : "Retrouve tes discussions enregistrées.");

        const user = field("Pseudo", "text", "username", "username");
        const pass = field("Mot de passe", "password", "password", registering ? "new-password" : "current-password");
        user.input.minLength = 3;
        user.input.maxLength = 32;
        pass.input.minLength = registering ? 8 : 1;
        pass.input.maxLength = 128;

        const hint = el("p", "", registering ? "Pseudo : 3 à 32 caractères (lettres, chiffres, _ . -). Mot de passe : 8 caractères minimum." : "");
        const error = el("p", "auth-error");
        error.setAttribute("role", "alert");

        const actions = el("div", "auth-actions");
        const cancel = el("button", "auth-secondary", "Fermer");
        cancel.type = "button";
        cancel.addEventListener("click", () => dialog.close());
        const submit = el("button", "auth-primary", registering ? "Créer mon compte" : "Se connecter");
        submit.type = "submit";
        actions.append(cancel, submit);

        form.addEventListener("submit", guard(submit, error, async () => {
            const call = registering ? ICC.services.register : ICC.services.login;
            const data = await call(user.input.value.trim(), pass.input.value);
            setUser(data.user);
            dialog.close();
        }));

        form.append(tabs, intro, user.wrap, pass.wrap);
        if (registering && !registrationOpen) {
            form.append(el("p", "auth-error", "Les inscriptions sont fermées sur ce serveur."));
        } else if (registering) {
            form.append(hint);
        }
        form.append(error, actions);
        return { form, focus: user.input };
    }

    function renderSignedIn() {
        const form = el("form", "auth-form");
        form.append(el("h2", "", currentUser.username));
        form.append(el("p", "", "Connecté. Tes discussions enregistrées apparaissent dans la barre de gauche."));

        // Mot de passe
        const pwd = el("div", "auth-section");
        pwd.append(el("h3", "", "Changer le mot de passe"));
        const current = field("Mot de passe actuel", "password", "current", "current-password");
        const next = field("Nouveau mot de passe", "password", "new", "new-password");
        next.input.minLength = 8;
        next.input.maxLength = 128;
        const pwdError = el("p", "auth-error");
        pwdError.setAttribute("role", "alert");
        const pwdOk = el("p", "");
        const pwdButton = el("button", "auth-secondary", "Mettre à jour");
        pwdButton.type = "button";
        pwdButton.addEventListener("click", guard(pwdButton, pwdError, async () => {
            if (!current.input.value || next.input.value.length < 8) {
                throw new Error("Renseigne le mot de passe actuel et un nouveau de 8 caractères minimum.");
            }
            await ICC.services.changePassword(current.input.value, next.input.value);
            current.input.value = "";
            next.input.value = "";
            pwdOk.textContent = "Mot de passe mis à jour.";
        }));
        current.input.required = false;
        next.input.required = false;
        pwd.append(current.wrap, next.wrap, pwdError, pwdOk, pwdButton);

        // Suppression
        const del = el("details", "auth-section");
        del.append(el("summary", "", "Supprimer mon compte"));
        del.append(el("p", "", "Ton compte et toutes tes discussions enregistrées seront effacés définitivement."));
        const confirm = field("Mot de passe", "password", "confirm", "current-password");
        confirm.input.required = false;
        const delError = el("p", "auth-error");
        delError.setAttribute("role", "alert");
        const delButton = el("button", "auth-primary danger", "Supprimer définitivement");
        delButton.type = "button";
        delButton.addEventListener("click", guard(delButton, delError, async () => {
            if (!confirm.input.value) throw new Error("Saisis ton mot de passe pour confirmer.");
            await ICC.services.deleteAccount(confirm.input.value);
            setUser(null);
            dialog.close();
        }));
        del.append(confirm.wrap, delError, delButton);

        const actions = el("div", "auth-actions");
        const close = el("button", "auth-secondary", "Fermer");
        close.type = "button";
        close.addEventListener("click", () => dialog.close());
        const out = el("button", "auth-primary", "Se déconnecter");
        out.type = "button";
        const outError = el("p", "auth-error");
        out.addEventListener("click", guard(out, outError, async () => {
            await ICC.services.logout();
            setUser(null);
            dialog.close();
        }));
        actions.append(close, out);

        form.append(pwd, del, outError, actions);
        form.addEventListener("submit", (event) => event.preventDefault());
        return { form, focus: null };
    }

    function render(mode) {
        const box = ensureDialog();
        const view = currentUser ? renderSignedIn() : renderSignedOut(mode || "login");
        box.replaceChildren(view.form);
        if (!box.open) box.showModal();
        if (view.focus) view.focus.focus();
    }

    ICC.auth = {
        init,
        user: () => currentUser,
        enabled: () => enabled,
        open: (mode) => render(mode),
        onChange: (fn) => listeners.push(fn),
        /* Session expirée côté serveur : on le reflète sans attendre. */
        expire: () => { if (currentUser) setUser(null); },
    };
})();
