// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Onglet « Bibliothèque » de l'administration : liste paginée des fichiers collectés, recherche
 * (nom / contenu), filtres, tri, visionneuse avec coloration, édition LOCALE (jamais envoyée sur
 * GitHub), historique avec restauration, reclassement manuel et analyse IA plafonnée.
 *
 * Gros fichiers : le serveur renvoie des tranches d'octets ; la visionneuse n'affiche qu'une
 * tranche à la fois (« Charger la suite ») et ne colore que ce qui reste raisonnable.
 *
 * `ICC.library.render(body)` dessine l'onglet ; `ICC.library.dispose()` le quitte.
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});
    const T = (text) => (ICC.i18n ? ICC.i18n.t(text) : text);
    const HIGHLIGHT_MAX = 60000;          // au-delà : texte brut (même limite que highlight.js)
    const SEARCH_DELAY = 350;

    const state = {
        q: "", scope: "name", language: "", category: "", subcategory: "", license: "", repo: "",
        status: "active", modified: "", sort: "collected_at", order: "desc", page: 1, page_size: 50,
    };
    let facets = null;
    let root = null;
    let dirty = false;                    // modification non enregistrée dans la visionneuse
    let searchTimer = null;

    const SORTS = [["collected_at", "Date de collecte"], ["path", "Chemin"], ["size", "Taille"],
        ["language", "Langage"], ["category", "Catégorie"], ["repo", "Dépôt"]];

    /* ----- utilitaires ----- */

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function icon(name, size) { return ICC.icons ? ICC.icons.make(name, size || 15) : document.createTextNode(""); }

    function btn(label, iconName, onClick, className) {
        const node = el("button", className || "adm-btn");
        node.type = "button";
        if (iconName) node.append(icon(iconName));
        if (label) node.append(el("span", "", label));
        if (!label) node.classList.add("adm-icon-btn");
        node.addEventListener("click", onClick);
        return node;
    }

    function exactSize(value) {
        return Number.isFinite(value) ? `${value.toLocaleString()} ${T("octets")}` : "—";
    }

    function when(value) {
        if (value === null || value === undefined || value === "") return "—";
        const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
        return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
    }

    function fail(error) {
        if (ICC.notify) ICC.notify(error && error.message ? error.message : T("Erreur inattendue."), "error");
    }

    async function copyText(text, label) {
        try {
            await navigator.clipboard.writeText(text);
            if (ICC.notify) ICC.notify(T(label || "Copié."), "ok");
        } catch {
            if (ICC.notify) ICC.notify(T("Copie impossible dans ce navigateur."), "error");
        }
    }

    function select(options, value, onChange, firstLabel) {
        const node = el("select", "adm-input adm-select");
        if (firstLabel !== undefined) node.append(new Option(firstLabel, ""));
        for (const [val, label] of options) node.append(new Option(label, val));
        node.value = value;
        node.addEventListener("change", () => onChange(node.value));
        return node;
    }

    function confirmLeave() {
        return !dirty || window.confirm(T("Des modifications ne sont pas enregistrées. Quitter quand même ?"));
    }

    /* ----- liste ----- */

    async function renderList() {
        dirty = false;
        root.replaceChildren(el("p", "adm-loading", T("Chargement…")));
        let page;
        try {
            if (!facets) facets = await ICC.services.library.facets();
            page = await ICC.services.library.files(state);
        } catch (error) {
            root.replaceChildren(el("p", "auth-error", error.message || T("Erreur inattendue.")));
            return;
        }

        const stats = el("div", "adm-stats");
        const card = (label, value) => {
            const c = el("div", "adm-stat");
            c.append(el("span", "adm-stat-value", value), el("span", "adm-stat-label", label));
            return c;
        };
        stats.append(card(T("Fichiers"), (facets.files ?? 0).toLocaleString()),
            card(T("Volume"), exactSize(facets.bytes)),
            card(T("Langages"), (facets.languages || []).length),
            card(T("Catégories"), (facets.categories || []).length));

        const search = el("input", "adm-input adm-search");
        search.type = "search";
        search.placeholder = T("Rechercher un nom, un chemin ou du contenu…");
        search.value = state.q;
        search.setAttribute("aria-label", T("Recherche"));
        search.addEventListener("input", () => {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(() => { state.q = search.value.trim(); state.page = 1; renderList(); }, SEARCH_DELAY);
        });
        const scope = select([["name", T("Nom / chemin")], ["content", T("Contenu")], ["all", T("Tout")]], state.scope,
            (v) => { state.scope = v; if (state.q) { state.page = 1; renderList(); } });

        const setFilter = (key) => (value) => { state[key] = value; if (key === "category") state.subcategory = ""; state.page = 1; renderList(); };
        const cats = (facets.categories || []).map((c) => [c.category, `${c.category} (${c.count})`]);
        const current = (facets.categories || []).find((c) => c.category === state.category);
        const subs = current ? current.subcategories.map((s) => [s.value, `${s.value} (${s.count})`]) : [];
        const filters = el("div", "adm-filters");
        filters.append(
            select((facets.languages || []).map((l) => [l.value, `${l.value} (${l.count})`]), state.language, setFilter("language"), T("Tous les langages")),
            select(cats, state.category, setFilter("category"), T("Toutes les catégories")),
            select(subs, state.subcategory, setFilter("subcategory"), T("Toutes les sous-catégories")),
            select((facets.licenses || []).map((l) => [l.value, `${l.value} (${l.count})`]), state.license, setFilter("license"), T("Toutes les licences")),
            select((facets.repos || []).map((r) => [r.value, r.value]), state.repo, setFilter("repo"), T("Tous les dépôts")),
            select([["active", T("Actifs")], ["deleted", T("Supprimés")]], state.status, setFilter("status")),
            select(SORTS.map(([v, l]) => [v, T(l)]), state.sort, setFilter("sort")),
            btn("", state.order === "asc" ? "up" : "down", () => { state.order = state.order === "asc" ? "desc" : "asc"; renderList(); }, "adm-btn adm-icon-btn"),
        );
        filters.lastChild.title = T("Inverser l'ordre de tri");

        const tools = el("div", "adm-toolbar");
        tools.append(search, scope, btn(T("Analyse IA"), "sparkles", openAi), btn("", "refresh", () => { facets = null; renderList(); }));
        tools.lastChild.title = T("Actualiser");

        const table = el("table", "adm-table adm-lib-table");
        const head = el("tr");
        [T("Fichier"), T("Dépôt"), T("Langage"), T("Catégorie"), T("Taille exacte"), T("Collecté"), T("SHA-256"), T("Licence")]
            .forEach((h) => head.append(el("th", "", h)));
        const thead = el("thead");
        thead.append(head);
        const rows = el("tbody");
        for (const item of page.items) {
            const row = el("tr", "adm-row");
            row.tabIndex = 0;
            const name = el("td", "adm-cell-name");
            name.append(el("strong", "", item.name));
            if (item.modified) name.append(el("span", "adm-tag", T("modifié")));
            name.append(el("small", "adm-muted adm-path", item.path));
            const category = el("td", "", item.category);
            if (item.subcategory) category.append(el("small", "adm-muted", ` / ${item.subcategory}`));
            if (item.classified_by && item.classified_by !== "auto") category.append(el("span", "adm-tag", item.classified_by));
            const hash = el("td", "adm-hash", (item.sha256 || "").slice(0, 10));
            hash.title = item.sha256 || "";
            const lang = el("td", "", item.language || "—");
            if (item.extension) lang.append(el("small", "adm-muted", ` .${item.extension}`));
            row.append(name, el("td", "adm-muted", item.repo), lang, category,
                el("td", "adm-num", exactSize(item.size)), el("td", "adm-muted", when(item.collected_at)), hash, el("td", "", item.license || "—"));
            const open = () => renderViewer(item.id);
            row.addEventListener("click", open);
            row.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
            rows.append(row);
        }
        table.append(thead, rows);
        const scroll = el("div", "adm-scroll");
        scroll.append(table);

        const pages = Math.max(1, Math.ceil(page.total / page.page_size));
        const pager = el("div", "adm-pager");
        const prev = btn(T("Précédent"), "left", () => { state.page -= 1; renderList(); });
        const next = btn(T("Suivant"), "right", () => { state.page += 1; renderList(); });
        prev.disabled = state.page <= 1;
        next.disabled = state.page >= pages;
        const size = select([["25", "25"], ["50", "50"], ["100", "100"], ["200", "200"]], String(state.page_size),
            (v) => { state.page_size = Number(v); state.page = 1; renderList(); });
        pager.append(prev, el("span", "adm-muted", `${T("Page")} ${state.page} / ${pages} · ${page.total.toLocaleString()} ${T("résultat(s)")}`), next, size);

        const nodes = [stats, tools, filters];
        if (page.partial) nodes.push(el("p", "adm-warn", T("La recherche dans le contenu a été limitée (trop de fichiers) : affine par filtre.")));
        if (!page.items.length) nodes.push(el("p", "adm-empty", T("Aucun fichier ne correspond. Lance une collecte depuis le panneau Crawler ou change les filtres.")));
        else nodes.push(scroll);
        nodes.push(pager);
        root.replaceChildren(...nodes);
    }

    /* ----- analyse IA ----- */

    async function openAi() {
        let info;
        try { info = await ICC.services.library.aiStatus(); } catch (error) { fail(error); return; }
        const box = el("dialog", "auth-dialog adm-sub-dialog");
        box.append(el("h3", "", T("Analyse IA des fichiers non classés")));
        box.append(el("p", "adm-muted", T("Envoie quelques fichiers au fournisseur IA actif pour en déduire catégorie, sous-catégorie et tags. Chaque fichier = une requête payante possible : l'analyse est limitée et jamais automatique. Un classement manuel n'est jamais écrasé.")));
        box.append(el("p", "adm-big", `${T("Aujourd'hui")} : ${info.used_today} / ${info.daily_max} · ${T("candidats")} : ${info.candidates} · ${T("max par lancement")} : ${info.max_batch}`));
        const count = el("input", "adm-input");
        count.type = "number"; count.min = "1"; count.max = String(info.max_batch); count.value = String(Math.min(5, info.max_batch));
        const out = el("p", "adm-status");
        const actions = el("div", "adm-actions");
        const run = btn(T("Analyser"), "sparkles", async () => {
            run.disabled = true;
            out.textContent = T("Analyse en cours…");
            try {
                const r = await ICC.services.library.aiRun(Number(count.value) || 1);
                out.textContent = `${r.classified} ${T("classé(s)")}, ${r.unchanged} ${T("sans changement")}, ${r.analysed} ${T("analysé(s)")}. ${(r.errors || []).join(" ")}`;
                facets = null;
            } catch (error) { out.textContent = error.message; out.dataset.kind = "error"; }
            run.disabled = false;
        }, "adm-btn adm-primary");
        run.disabled = !info.enabled || info.remaining_today <= 0;
        actions.append(count, run, btn(T("Fermer"), "", () => { box.close(); renderList(); }));
        box.append(actions, out);
        box.addEventListener("close", () => box.remove());
        document.body.append(box);
        box.showModal();
    }

    /* ----- visionneuse ----- */

    async function renderViewer(id) {
        root.replaceChildren(el("p", "adm-loading", T("Chargement…")));
        let item;
        let data;
        try {
            item = await ICC.services.library.file(id);
            data = item.status === "deleted" || item.missing ? null : await ICC.services.library.content(id, 0);
        } catch (error) {
            fail(error);
            renderList();
            return;
        }

        const back = btn(T("Retour à la liste"), "left", () => { if (confirmLeave()) renderList(); });
        const title = el("div", "adm-viewer-title");
        title.append(el("strong", "", item.name), el("small", "adm-muted adm-path", `${item.repo} · ${item.path}`));
        const head = el("div", "adm-viewer-head");
        head.append(back, title);

        const meta = el("dl", "adm-meta");
        const pair = (k, v) => { meta.append(el("dt", "", T(k)), el("dd", "", v)); };
        pair("Langage", item.language || "—");
        pair("Catégorie", item.subcategory ? `${item.category} / ${item.subcategory}` : item.category);
        pair("Tags", (item.tags || []).join(", ") || "—");
        pair("Taille exacte", exactSize(item.size));
        pair("Collecté", when(item.collected_at));
        pair("Licence", item.license || "—");
        pair("Branche / commit", `${item.branch || "—"} · ${(item.commit || "").slice(0, 10) || "—"}`);
        const hashCell = el("dd", "adm-hash", item.local_sha256 || item.sha256);
        meta.append(el("dt", "", "SHA-256"), hashCell);
        if (item.modified) pair("Original GitHub", item.sha256);

        const actions = el("div", "adm-actions adm-viewer-actions");
        const view = el("div", "adm-code-wrap");
        const info = el("p", "adm-status");
        let editor = null;
        let loaded = data;

        function showCode(text, language) {
            view.replaceChildren();
            const pre = el("pre", "adm-code");
            const code = el("code");
            if (ICC.highlight && text.length <= HIGHLIGHT_MAX) code.append(ICC.highlight.render(text, language));
            else code.textContent = text;
            pre.append(code);
            view.append(pre);
        }

        if (!data) {
            view.append(el("p", "adm-empty", item.status === "deleted" ? T("Ce fichier a été supprimé localement (une version peut être restaurée).") : T("Le fichier local est introuvable sur le disque.")));
        } else {
            showCode(data.content, item.language);
            if (data.truncated) {
                // Fichier volumineux : suite chargée tranche par tranche, ajoutée en texte brut.
                const more = btn(T("Charger la suite"), "down", async () => {
                    more.disabled = true;
                    try {
                        const next = await ICC.services.library.content(id, loaded.offset + loaded.bytes_read);
                        const part = el("pre", "adm-code");
                        part.textContent = next.content;
                        view.insertBefore(part, more);
                        loaded = next;
                        info.textContent = `${next.offset + next.bytes_read} / ${next.size} ${T("octets affichés")}`;
                        if (!next.truncated) more.remove();
                    } catch (error) { fail(error); }
                    more.disabled = false;
                });
                view.append(more);
                info.textContent = `${data.bytes_read} / ${data.size} ${T("octets affichés (fichier volumineux, lecture seule)")}`;
            }
        }

        if (data) {
            actions.append(btn(T("Copier"), "copy", () => copyText(editor ? editor.value : data.content, "Contenu copié.")));
            const dl = el("a", "adm-btn");
            dl.href = ICC.services.library.downloadUrl(id);
            dl.setAttribute("download", item.name);
            dl.append(icon("download"), el("span", "", T("Télécharger")));
            actions.append(dl);
            if (data.editable) {
                const edit = btn(T("Modifier"), "pencil", () => {
                    editor = el("textarea", "adm-area adm-editor");
                    editor.value = data.content;
                    editor.spellcheck = false;
                    editor.addEventListener("input", () => { dirty = editor.value !== data.content; });
                    view.replaceChildren(editor);
                    edit.hidden = true;
                    save.hidden = false;
                    cancel.hidden = false;
                    editor.focus();
                });
                const save = btn(T("Enregistrer en local"), "save", async () => {
                    save.disabled = true;
                    try {
                        await ICC.services.library.save(id, editor.value, data.sha256);
                        dirty = false;
                        ICC.notify(T("Enregistré en local (GitHub n'est jamais modifié)."), "ok");
                        renderViewer(id);
                    } catch (error) {
                        fail(error);
                        if (error.status === 409) info.textContent = T("Le fichier a changé depuis l'ouverture : copie ton texte puis recharge.");
                        save.disabled = false;
                    }
                }, "adm-btn adm-primary");
                const cancel = btn(T("Annuler"), "x", () => { if (confirmLeave()) { dirty = false; renderViewer(id); } });
                save.hidden = true;
                cancel.hidden = true;
                actions.append(edit, save, cancel);
            } else if (!data.truncated) {
                info.textContent = T("Fichier trop volumineux ou binaire : modification désactivée.");
            }
        }
        actions.append(btn(T("Historique"), "history", () => renderHistory(item, panel)),
            btn(T("Catégorie"), "tag", () => renderCategory(item, panel)));
        if (item.status !== "deleted") {
            actions.append(btn(T("Supprimer"), "trash", async () => {
                if (!window.confirm(T(`Supprimer « ${item.path} » de la bibliothèque locale ? Une version reste restaurable.`))) return;
                try {
                    await ICC.services.library.remove(id);
                    ICC.notify(T("Fichier supprimé."), "ok");
                    facets = null;
                    renderList();
                } catch (error) { fail(error); }
            }, "adm-btn adm-danger"));
        }
        const panel = el("div", "adm-side");
        root.replaceChildren(head, meta, actions, info, panel, view);
    }

    async function renderHistory(item, panel) {
        panel.replaceChildren(el("p", "adm-loading", T("Chargement…")));
        try {
            const { versions } = await ICC.services.library.versions(item.id);
            const list = el("ul", "adm-list");
            if (!versions.length) list.append(el("li", "adm-muted", T("Aucune version enregistrée.")));
            for (const v of versions) {
                const li = el("li");
                li.append(el("span", "", `${when(v.created_at)} · ${v.reason} · ${exactSize(v.size)}`),
                    btn(T("Voir"), "file", async () => {
                        try {
                            const full = await ICC.services.library.version(item.id, v.id);
                            const pre = el("pre", "adm-code");
                            pre.textContent = full.content;
                            panel.replaceChildren(btn(T("Fermer"), "x", () => renderHistory(item, panel)), pre);
                        } catch (error) { fail(error); }
                    }),
                    btn(T("Restaurer"), "restore", async () => {
                        if (!window.confirm(T("Restaurer cette version ? Le contenu actuel est conservé dans l'historique."))) return;
                        try {
                            await ICC.services.library.restore(item.id, v.id);
                            ICC.notify(T("Version restaurée."), "ok");
                            dirty = false;
                            renderViewer(item.id);
                        } catch (error) { fail(error); }
                    }));
                list.append(li);
            }
            panel.replaceChildren(el("h3", "", T("Historique des modifications")), list);
        } catch (error) { panel.replaceChildren(); fail(error); }
    }

    function renderCategory(item, panel) {
        const category = el("input", "adm-input");
        category.value = item.category === "non-classe" ? "" : item.category;
        category.placeholder = T("catégorie (ex. reseau)");
        category.setAttribute("list", "adm-cat-list");
        const list = el("datalist");
        list.id = "adm-cat-list";
        for (const c of (facets && facets.categories) || []) list.append(new Option(c.category));
        const sub = el("input", "adm-input");
        sub.value = item.subcategory || "";
        sub.placeholder = T("sous-catégorie (facultatif)");
        const tags = el("input", "adm-input");
        tags.value = (item.tags || []).join(", ");
        tags.placeholder = T("tags séparés par des virgules");
        const save = btn(T("Enregistrer le classement"), "save", async () => {
            const value = category.value.trim() || "non-classe";
            try {
                await ICC.services.library.setCategory(item.id, value, sub.value.trim(),
                    tags.value.split(",").map((t) => t.trim()).filter(Boolean));
                facets = null;
                ICC.notify(T("Classement mis à jour (fichier déplacé)."), "ok");
                renderViewer(item.id);
            } catch (error) { fail(error); }
        }, "adm-btn adm-primary");
        panel.replaceChildren(el("h3", "", T("Catégorie et tags")),
            el("p", "adm-muted", T("Laisse vide pour « non-classe ». Le fichier est déplacé dans son dossier de catégorie.")),
            list, category, sub, tags, save);
    }

    ICC.library = {
        render(body) {
            root = body;
            return renderList();
        },
        dispose() {
            window.clearTimeout(searchTimer);
            dirty = false;
            root = null;
            facets = null;
        },
        isDirty: () => dirty,
    };
})();
