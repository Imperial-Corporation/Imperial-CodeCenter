// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Panneau de configuration globale des fournisseurs IA.
 *
 * La configuration est partagée par toute l'instance : un seul fournisseur
 * et un seul modèle sont actifs. Le serveur ne renvoie jamais une clé API ;
 * il indique seulement si une clé est enregistrée (`api_key_set`).
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function icon(provider) {
        const node = el("span", "provider-icon", provider.glyph);
        node.style.setProperty("--accent-provider", provider.accent);
        node.setAttribute("aria-hidden", "true");
        return node;
    }

    function field(id, label, input, hint) {
        const wrap = el("div", "sp-field");
        const lab = el("label", "", label);
        lab.htmlFor = id;
        input.id = id;
        wrap.append(lab, input);
        if (hint) wrap.append(el("small", "sp-hint", hint));
        return wrap;
    }

    function init() {
        const services = ICC.services;
        if (!services) return;

        let catalog = null;
        let saved = null;      // configuration publique enregistrée
        let current = null;    // identifiant du fournisseur affiché
        let busy = false;
        const drafts = {};     // valeurs saisies par fournisseur (sans clé)

        /* ----- bouton flottant ----- */

        const fab = el("button", "settings-fab");
        fab.type = "button";
        fab.title = "Configuration de l'IA";
        fab.setAttribute("aria-label", "Ouvrir la configuration de l'IA");
        fab.setAttribute("aria-haspopup", "dialog");
        fab.setAttribute("aria-expanded", "false");
        fab.innerHTML =
            '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" ' +
            'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="3"></circle>' +
            '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>' +
            "</svg>";

        /* ----- panneau ----- */

        const panel = el("section", "settings-panel");
        panel.id = "settings-panel";
        panel.hidden = true;
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-labelledby", "sp-title");
        fab.setAttribute("aria-controls", panel.id);

        const head = el("div", "sp-head");
        const title = el("h2", "", "Fournisseur IA");
        title.id = "sp-title";
        const close = el("button", "sp-close", "×");
        close.type = "button";
        close.setAttribute("aria-label", "Fermer");
        head.append(title, close);

        const intro = el("p", "sp-intro",
            "Configuration globale : un seul fournisseur et un seul modèle sont actifs pour tous les utilisateurs.");

        const body = el("form", "sp-body");
        body.noValidate = true;

        /* liste déroulante avec icônes (un <select> ne peut pas en afficher) */
        const pickerWrap = el("div", "sp-field");
        const pickerLabel = el("span", "sp-label", "Fournisseur");
        pickerLabel.id = "sp-picker-label";
        const picker = el("div", "sp-picker");
        const pickerButton = el("button", "sp-picker-button");
        pickerButton.type = "button";
        pickerButton.setAttribute("aria-haspopup", "listbox");
        pickerButton.setAttribute("aria-expanded", "false");
        pickerButton.setAttribute("aria-labelledby", "sp-picker-label");
        const list = el("ul", "sp-picker-list");
        list.setAttribute("role", "listbox");
        list.setAttribute("aria-labelledby", "sp-picker-label");
        list.tabIndex = -1;
        list.hidden = true;
        picker.append(pickerButton, list);
        pickerWrap.append(pickerLabel, picker);

        const urlInput = el("input");
        urlInput.type = "url";
        urlInput.autocomplete = "off";
        urlInput.spellcheck = false;

        const modelInput = el("input");
        modelInput.type = "text";
        modelInput.autocomplete = "off";
        modelInput.spellcheck = false;
        const modelList = el("datalist");
        modelList.id = "sp-models";
        modelInput.setAttribute("list", modelList.id);

        const keyInput = el("input");
        keyInput.type = "password";
        keyInput.autocomplete = "new-password";
        keyInput.spellcheck = false;
        const clearKey = el("input");
        clearKey.type = "checkbox";
        const clearWrap = el("label", "sp-check");
        clearWrap.append(clearKey, document.createTextNode(" Supprimer la clé enregistrée"));

        const notes = el("p", "sp-notes");

        const advanced = el("details", "sp-advanced");
        advanced.append(el("summary", "", "Advanced features"));
        const advancedBox = el("div", "sp-advanced-grid");
        advanced.append(advancedBox);

        const actions = el("div", "sp-actions");
        const testButton = el("button", "sp-button", "Tester la connexion");
        testButton.type = "button";
        const saveButton = el("button", "sp-button sp-primary", "Enregistrer et activer");
        saveButton.type = "submit";
        actions.append(testButton, saveButton);

        const status = el("p", "sp-status");
        status.setAttribute("role", "status");
        status.setAttribute("aria-live", "polite");

        const urlField = field("sp-url", "URL de l'API", urlInput);
        const modelField = field("sp-model", "Modèle", modelInput);
        modelField.append(modelList);
        const keyField = field("sp-key", "Clé API", keyInput);
        keyField.append(clearWrap);

        body.append(pickerWrap, urlField, modelField, keyField, notes, advanced, actions, status);
        panel.append(head, intro, body);
        document.body.append(fab, panel);

        /* ----- état et affichage ----- */

        function providerSpec() {
            return catalog.providers.find((p) => p.id === current);
        }

        function setStatus(message, kind) {
            status.textContent = message || "";
            status.dataset.kind = kind || "";
        }

        function setBusy(value) {
            busy = value;
            testButton.disabled = value;
            saveButton.disabled = value;
        }

        function renderPickerButton() {
            const spec = providerSpec();
            pickerButton.replaceChildren(icon(spec), el("span", "sp-picker-name", spec.label),
                el("span", "sp-picker-caret", "▾"));
        }

        function renderPickerList() {
            list.replaceChildren();

            catalog.providers.forEach((spec) => {
                const item = el("li", "sp-picker-option");
                item.setAttribute("role", "option");
                item.id = `sp-opt-${spec.id}`;
                item.dataset.id = spec.id;
                item.tabIndex = -1;
                item.setAttribute("aria-selected", String(spec.id === current));

                item.append(icon(spec), el("span", "sp-picker-name", spec.label),
                    el("span", "sp-picker-kind", spec.kind === "local" ? "local" : "distant"));
                if (saved && saved.active === spec.id) item.append(el("span", "sp-picker-active", "actif"));

                item.addEventListener("click", () => choose(spec.id));
                list.append(item);
            });
        }

        function togglePicker(open) {
            list.hidden = !open;
            pickerButton.setAttribute("aria-expanded", String(open));

            if (open) {
                const selected = list.querySelector('[aria-selected="true"]') || list.firstElementChild;
                if (selected) selected.focus();
            }
        }

        function saveDraft() {
            if (!current || !catalog) return;
            drafts[current] = {
                base_url: urlInput.value,
                model: modelInput.value,
                advanced: readAdvanced(false),
            };
        }

        function choose(providerId) {
            saveDraft();
            current = providerId;
            togglePicker(false);
            fillForm();
            pickerButton.focus();
        }

        function buildAdvanced(spec) {
            advancedBox.replaceChildren();

            catalog.advanced_fields.forEach((f) => {
                const input = el("input");
                input.type = "number";
                input.min = String(f.minimum);
                input.max = String(f.maximum);
                input.step = f.kind === "float" ? "0.1" : "1";
                input.dataset.key = f.key;
                input.dataset.kind = f.kind;
                advancedBox.append(field(`sp-adv-${f.key}`, f.label, input, f.help));
            });
        }

        function readAdvanced(strict) {
            const values = {};

            advancedBox.querySelectorAll("input").forEach((input) => {
                if (input.value === "") return;
                const number = input.dataset.kind === "float" ? parseFloat(input.value) : parseInt(input.value, 10);
                if (Number.isFinite(number)) values[input.dataset.key] = number;
                else if (strict) throw new Error("Un réglage avancé n'est pas un nombre valide.");
            });

            return values;
        }

        function fillForm() {
            const spec = providerSpec();
            const stored = saved && saved.providers[current];
            const draft = drafts[current];

            renderPickerButton();
            renderPickerList();
            buildAdvanced(spec);

            urlInput.value = (draft && draft.base_url) || (stored && stored.base_url) || spec.default_base_url;
            modelInput.value = (draft && draft.model) || (stored && stored.model) || "";
            modelInput.placeholder = spec.suggested_model || "nom du modèle";
            modelList.replaceChildren();

            const values = Object.assign({}, spec.advanced_defaults, stored && stored.advanced, draft && draft.advanced);
            advancedBox.querySelectorAll("input").forEach((input) => {
                input.value = values[input.dataset.key] ?? "";
            });

            const hasKey = Boolean(stored && stored.api_key_set);
            keyInput.value = "";
            clearKey.checked = false;
            clearWrap.hidden = !hasKey;
            keyField.querySelector("label").textContent = spec.needs_api_key ? "Clé API" : "Clé API (facultative)";
            keyInput.placeholder = hasKey ? "•••••••• (clé enregistrée, laisser vide pour la conserver)" : "";

            notes.textContent = spec.notes || "";
            notes.hidden = !spec.notes;
            setStatus("", "");
        }

        function buildPayload(activate) {
            const spec = providerSpec();
            const payload = {
                base_url: urlInput.value.trim(),
                model: modelInput.value.trim(),
                advanced: readAdvanced(true),
                activate,
            };

            if (!payload.base_url) throw new Error("Renseigne l'URL de l'API.");
            if (!payload.model) throw new Error("Renseigne le modèle.");

            const typed = keyInput.value.trim();
            const hasStored = Boolean(saved && saved.providers[current] && saved.providers[current].api_key_set);

            if (typed) payload.api_key = typed;
            if (clearKey.checked) payload.clear_api_key = true;

            if (spec.needs_api_key && !typed && (!hasStored || clearKey.checked)) {
                throw new Error("Ce fournisseur exige une clé API.");
            }

            return payload;
        }

        function handleError(error) {
            if (error.status === 401) {
                ICC.auth.expire();
                closePanel(false);
                ICC.auth.open("login", "Ta session a expiré : reconnecte-toi pour retrouver tes clés.");
            } else {
                setStatus(error.message || "Une erreur est survenue.", "error");
            }
        }

        /* ----- actions ----- */

        async function load() {
            setStatus("Chargement…", "info");
            setBusy(true);

            try {
                if (!catalog) catalog = await services.aiCatalog();
                saved = await services.aiConfig();
                current = saved.active && catalog.providers.some((p) => p.id === saved.active)
                    ? saved.active
                    : (current || catalog.providers[0].id);
                fillForm();
            } catch (error) {
                if (catalog && !current) {
                    current = catalog.providers[0].id;
                    fillForm();
                }
                handleError(error);
            } finally {
                setBusy(false);
            }
        }

        testButton.addEventListener("click", async () => {
            if (busy) return;
            let payload;

            try {
                payload = buildPayload(false);
            } catch (error) {
                setStatus(error.message, "error");
                return;
            }

            setBusy(true);
            setStatus("Test en cours…", "info");

            try {
                const result = await services.aiTest(current, payload);

                if (result.ok) {
                    const extra = Number.isFinite(result.latency_ms) ? ` (${result.latency_ms} ms)` : "";
                    setStatus(`${result.message}${extra}`, "ok");
                    modelList.replaceChildren(
                        ...(result.models || []).slice(0, 200).map((name) => {
                            const option = el("option");
                            option.value = name;
                            return option;
                        }),
                    );
                } else {
                    setStatus(result.message || "Échec de la connexion.", "error");
                }
            } catch (error) {
                handleError(error);
            } finally {
                setBusy(false);
            }
        });

        body.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (busy) return;
            let payload;

            try {
                payload = buildPayload(true);
            } catch (error) {
                setStatus(error.message, "error");
                return;
            }

            setBusy(true);
            setStatus("Enregistrement…", "info");

            try {
                saved = await services.aiSave(current, payload);
                delete drafts[current];
                fillForm();
                setStatus(`${providerSpec().label} est maintenant le fournisseur actif.`, "ok");
                document.dispatchEvent(new CustomEvent("icc:ai-config-changed", { detail: { active: saved.active } }));
            } catch (error) {
                handleError(error);
            } finally {
                setBusy(false);
            }
        });

        /* ----- ouverture, fermeture, clavier ----- */

        function openPanel() {
            if (!ICC.auth.user()) {
                ICC.auth.open("login", "Connecte-toi (ou crée un compte) : tes clés IA sont chiffrées avec ton mot de passe et ne sont utilisables que par toi.");
                return;
            }
            panel.hidden = false;
            fab.setAttribute("aria-expanded", "true");
            load().then(() => pickerButton.focus());
        }

        function closePanel(refocus = true) {
            panel.hidden = true;
            togglePicker(false);
            fab.setAttribute("aria-expanded", "false");
            if (refocus) fab.focus();
        }

        // Changement de compte : on ne garde rien en mémoire de la configuration de l'autre.
        ICC.auth.onChange(() => {
            saved = null;
            if (!panel.hidden) closePanel(false);
        });

        fab.addEventListener("click", () => (panel.hidden ? openPanel() : closePanel()));
        close.addEventListener("click", closePanel);

        pickerButton.addEventListener("click", () => togglePicker(list.hidden));

        pickerButton.addEventListener("keydown", (event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                togglePicker(true);
            }
        });

        list.addEventListener("keydown", (event) => {
            const items = Array.from(list.children);
            const index = items.indexOf(document.activeElement);

            if (event.key === "ArrowDown") {
                event.preventDefault();
                items[Math.min(index + 1, items.length - 1)].focus();
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                items[Math.max(index - 1, 0)].focus();
            } else if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                if (index >= 0) choose(items[index].dataset.id);
            } else if (event.key === "Escape") {
                event.stopPropagation();
                togglePicker(false);
                pickerButton.focus();
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !panel.hidden && list.hidden) closePanel();
        });

        document.addEventListener("click", (event) => {
            if (!list.hidden && !picker.contains(event.target)) togglePicker(false);
        });
    }

    document.addEventListener("DOMContentLoaded", init);
})();
