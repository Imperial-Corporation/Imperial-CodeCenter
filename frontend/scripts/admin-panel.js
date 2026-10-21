// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Administration (réservée au premier compte créé) : comptes et inscriptions,
 * indexation (progression en direct, rescan, exclusions), ressources du serveur
 * et sauvegardes. Ouvert depuis la fenêtre « Compte » → « Administration ».
 *
 * `ICC.admin` : { open }
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});
    const T = (text) => (ICC.i18n ? ICC.i18n.t(text) : text);

    const TABS = [
        ["users", "Comptes"],
        ["index", "Index"],
        ["system", "Système"],
        ["backups", "Sauvegardes"],
    ];

    let dialog = null;
    let timer = null;
    let current = "users";

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function bytes(value) {
        if (!Number.isFinite(value) || value === null) return "—";
        const en = ICC.i18n && ICC.i18n.lang() === "en";
        if (value < 1024 ** 2) return `${(value / 1024).toFixed(0)} ${en ? "KB" : "Ko"}`;
        if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} ${en ? "MB" : "Mo"}`;
        return `${(value / 1024 ** 3).toFixed(2)} ${en ? "GB" : "Go"}`;
    }

    function date(seconds) {
        if (!seconds) return "—";
        return new Date(seconds * 1000).toLocaleString();
    }

    function duration(seconds) {
        const s = Math.round(seconds || 0);
        if (s < 60) return `${s} s`;
        if (s < 3600) return `${Math.floor(s / 60)} min`;
        if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
        return `${Math.floor(s / 86400)} ${ICC.i18n && ICC.i18n.lang() === "en" ? "d" : "j"} ${Math.floor((s % 86400) / 3600)} h`;
    }

    function button(label, onClick, className) {
        const node = el("button", className || "adm-btn", label);
        node.type = "button";
        node.addEventListener("click", onClick);
        return node;
    }

    function stopTimer() {
        window.clearTimeout(timer);
        timer = null;
    }

    function note(box, message, kind) {
        box.textContent = message || "";
        box.dataset.kind = kind || "";
    }

    async function run(box, task, success) {
        note(box, "");
        try {
            const result = await task();
            if (success) note(box, success, "ok");
            return result;
        } catch (error) {
            note(box, error.message || "Erreur inattendue.", "error");
            return null;
        }
    }

    /* ----- Comptes ----- */

    async function renderUsers(body) {
        const status = el("p", "adm-status");
        const data = await ICC.services.admin.users();
        body.replaceChildren();

        const reg = el("label", "adm-check");
        const box = el("input");
        box.type = "checkbox";
        box.checked = data.registration_open;
        box.addEventListener("change", async () => {
            const ok = await run(status, () => ICC.services.admin.setRegistration(box.checked),
                box.checked ? "Inscriptions ouvertes." : "Inscriptions fermées.");
            if (!ok) box.checked = !box.checked;
        });
        reg.append(box, el("span", "", "Inscriptions ouvertes (n'importe qui peut créer un compte)"));

        const table = el("table", "adm-table");
        const head = el("tr");
        ["Compte", "Créé", "Dernière connexion", "Discussions", "Jetons", ""].forEach((h) => head.append(el("th", "", h)));
        const thead = el("thead");
        thead.append(head);
        table.append(thead);
        const rows = el("tbody");
        data.users.forEach((u) => {
            const row = el("tr");
            if (u.disabled) row.dataset.disabled = "true";
            const name = el("td", "", u.username);
            if (u.is_admin) name.append(el("span", "adm-tag", "admin"));
            if (u.disabled) name.append(el("span", "adm-tag adm-tag-off", "désactivé"));
            row.append(name, el("td", "", date(u.created_at)), el("td", "", date(u.last_login)),
                el("td", "", u.chats), el("td", "", u.tokens));
            const actions = el("td", "adm-actions");
            if (!u.is_admin) {
                actions.append(
                    button(u.disabled ? "Réactiver" : "Désactiver", async () => {
                        await run(status, () => ICC.services.admin.setDisabled(u.id, !u.disabled));
                        refresh();
                    }),
                    button("Mot de passe", async () => {
                        const value = window.prompt(T(`Nouveau mot de passe pour ${u.username} (8 caractères minimum).\nSes clés IA chiffrées seront effacées ; ses discussions restent.`));
                        if (!value) return;
                        await run(status, () => ICC.services.admin.resetPassword(u.id, value), "Mot de passe réinitialisé.");
                    }),
                    button("Supprimer", async () => {
                        if (!window.confirm(T(`Supprimer définitivement le compte « ${u.username} » et ses discussions ?`))) return;
                        await run(status, () => ICC.services.admin.deleteUser(u.id));
                        refresh();
                    }, "adm-btn adm-danger"),
                );
            }
            row.append(actions);
            rows.append(row);
        });
        table.append(rows);
        body.append(reg, el("div", "adm-scroll"), status);
        body.querySelector(".adm-scroll").append(table);
    }

    /* ----- Index ----- */

    function progressBar(scan) {
        const wrap = el("div", "adm-progress");
        const total = scan.previous_total || 0;
        const done = (scan.indexed || 0) + (scan.unchanged || 0) + (scan.failed || 0) + (scan.excluded || 0);
        const fill = el("div", "adm-progress-fill");
        const percent = scan.running && total > 0 ? Math.min(100, Math.round((done / total) * 100)) : (scan.running ? null : 100);
        if (percent === null) wrap.dataset.indeterminate = "true";
        else fill.style.width = `${percent}%`;
        wrap.append(fill);
        return wrap;
    }

    async function renderIndex(body) {
        const [info, ex] = await Promise.all([ICC.services.admin.index(), ICC.services.admin.exclusions()]);
        const scan = info.scan || {};
        const stats = info.stats || {};
        const status = el("p", "adm-status");
        const live = el("div", "adm-live");

        const state = scan.running
            ? `Passage en cours (${scan.kind || "…"}) : ${scan.seen || 0} fichiers vus, ${scan.indexed || 0} indexés, ${scan.unchanged || 0} inchangés`
            : (scan.finished_unix ? `Dernier passage : ${date(scan.finished_unix)} en ${duration(scan.last_duration_secs)}` : "Aucun passage terminé depuis le démarrage.");
        live.append(el("p", "adm-big", state), progressBar(scan));
        if (scan.running && scan.current) live.append(el("small", "adm-muted", scan.current));
        if (scan.failed) live.append(el("small", "adm-warn", `${scan.failed} fichier(s) en échec`));
        if (scan.excluded) live.append(el("small", "adm-muted", `${scan.excluded} fichier(s) exclu(s)`));
        if (scan.pause_ms) live.append(el("small", "adm-muted", `Pause entre fichiers : ${scan.pause_ms} ms (profil léger)`));

        const counts = el("p", "adm-muted", `Documents indexés : ${stats.documents_total ?? "—"}`);
        const rescan = button("Relancer l'indexation maintenant", async () => {
            await run(status, () => ICC.services.admin.rescan(), "Indexation demandée.");
            window.setTimeout(refresh, 800);
        });

        const exclTitle = el("h3", "", "Exclusions");
        const exclHelp = el("p", "adm-muted",
            "Un motif par ligne. Sans « / » : nom de fichier ou de dossier (*.min.js, node_modules). " +
            "Avec « / » : suite de dossiers (vendor/legacy). Les fichiers déjà indexés qui deviennent exclus sont retirés de l'index.");
        const area = el("textarea", "adm-area");
        area.rows = 6;
        area.value = ex.patterns.join("\n");
        area.spellcheck = false;
        const save = button("Enregistrer les exclusions", async () => {
            const patterns = area.value.split("\n").map((l) => l.trim()).filter(Boolean);
            const saved = await run(status, () => ICC.services.admin.setExclusions(patterns),
                "Exclusions enregistrées : réindexation lancée.");
            if (saved) area.value = saved.patterns.join("\n");
        });
        body.replaceChildren(live, counts, rescan, exclTitle, exclHelp, area, save, status);
        if (ex.env) body.append(el("small", "adm-muted", `Exclusions de l'environnement (non modifiables ici) : ${ex.env}`));

        if (scan.running) timer = window.setTimeout(refresh, 1000);
    }

    /* ----- Système ----- */

    function meter(label, value, total, text) {
        const row = el("div", "adm-meter");
        row.append(el("span", "", label));
        const bar = el("div", "adm-progress");
        const fill = el("div", "adm-progress-fill");
        fill.style.width = total ? `${Math.min(100, Math.round((value / total) * 100))}%` : "0%";
        bar.append(fill);
        row.append(bar, el("span", "adm-muted", text));
        return row;
    }

    async function renderSystem(body) {
        const s = await ICC.services.admin.system();
        const m = s.machine || {};
        const p = s.process || {};
        const d = (s.disk || {}).data;
        const cd = (s.disk || {}).codes;
        const items = [];
        items.push(el("p", "adm-big", `Profil : ${(s.profile && s.profile.profile) || "standard"} · en marche depuis ${duration(s.uptime_s)}`));
        if (m.mem_total) {
            const used = m.mem_total - (m.mem_available || 0);
            items.push(meter("Mémoire machine", used, m.mem_total, `${bytes(used)} / ${bytes(m.mem_total)}`));
        }
        items.push(el("p", "adm-muted", `Processus CodeCenter : ${bytes(p.rss)} (pic ${bytes(p.rss_peak)}) · ${p.threads} threads`));
        if (m.load) items.push(el("p", "adm-muted", `Charge : ${m.load.map((v) => v.toFixed(2)).join(" · ")} (${m.cpus} cœur(s))`));
        if (d) items.push(meter("Disque des données", d.used, d.total, `${bytes(d.free)} libres`));
        if (cd) items.push(meter("Disque de la bibliothèque", cd.used, cd.total, `${bytes(cd.free)} libres`));
        items.push(el("p", "adm-muted", `Base des comptes : ${bytes((s.disk || {}).accounts_db)}`));
        items.push(el("p", "adm-muted", "Métriques Prometheus : /metrics (jeton CODECENTER_METRICS_TOKEN)."));
        body.replaceChildren(...items);
        timer = window.setTimeout(refresh, 5000);
    }

    /* ----- Sauvegardes ----- */

    async function renderBackups(body) {
        const data = await ICC.services.admin.backups();
        const status = el("p", "adm-status");

        const withCodes = el("label", "adm-check");
        const codes = el("input");
        codes.type = "checkbox";
        withCodes.append(codes, el("span", "", "Inclure la bibliothèque de code (volumineux)"));
        const pass = el("input", "adm-input");
        pass.type = "password";
        pass.placeholder = "Phrase secrète (facultative, 8 caractères minimum) — chiffre la sauvegarde";
        pass.autocomplete = "new-password";
        const create = button("Créer une sauvegarde", async () => {
            create.disabled = true;
            await run(status, () => ICC.services.admin.createBackup(codes.checked, pass.value), "Sauvegarde créée.");
            create.disabled = false;
            refresh();
        }, "adm-btn adm-primary");

        const list = el("ul", "adm-list");
        if (!data.backups.length) list.append(el("li", "adm-muted", "Aucune sauvegarde."));
        data.backups.forEach((b) => {
            const li = el("li");
            const link = el("a", "", b.name);
            link.href = ICC.services.admin.backupUrl(b.name);
            li.append(link, el("span", "adm-muted", ` · ${bytes(b.size)}${b.encrypted ? " · chiffrée" : ""} · ${date(b.created)}`),
                button("Supprimer", async () => {
                    if (!window.confirm(T(`Supprimer ${b.name} ?`))) return;
                    await run(status, () => ICC.services.admin.deleteBackup(b.name));
                    refresh();
                }, "adm-btn adm-danger"));
            list.append(li);
        });
        const help = el("p", "adm-muted",
            "Sans phrase secrète, la sauvegarde contient les empreintes de mots de passe et la configuration IA chiffrée des comptes : garde-la privée. " +
            `Planification : CODECENTER_BACKUP_EVERY_HOURS (actuellement ${data.every_hours} h, 0 = désactivée), ${data.keep} conservées. ` +
            "La restauration se fait serveur arrêté : python -m icc_ai.backup restore (voir docs/sauvegardes.md).");
        body.replaceChildren(withCodes, pass, create, status, list, help);
    }

    const VIEWS = { users: renderUsers, index: renderIndex, system: renderSystem, backups: renderBackups };

    async function refresh() {
        stopTimer();
        if (!dialog || !dialog.open) return;
        const body = dialog.querySelector(".adm-body");
        try {
            await VIEWS[current](body);
        } catch (error) {
            if (error.status === 401 || error.status === 403) {
                body.replaceChildren(el("p", "auth-error", error.message));
            } else {
                body.replaceChildren(el("p", "auth-error", error.message || "Erreur inattendue."));
            }
        }
    }

    function open() {
        if (!dialog) {
            dialog = el("dialog", "auth-dialog adm-dialog");
            dialog.setAttribute("aria-label", "Administration");
            dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
            dialog.addEventListener("close", stopTimer);
            document.body.append(dialog);
        }
        const head = el("div", "adm-head");
        head.append(el("h2", "", "Administration"));
        head.append(button("Fermer", () => dialog.close(), "auth-secondary"));
        const tabs = el("div", "auth-tabs");
        tabs.setAttribute("role", "tablist");
        TABS.forEach(([id, label]) => {
            const tab = el("button", "auth-tab", label);
            tab.type = "button";
            tab.setAttribute("role", "tab");
            tab.setAttribute("aria-selected", String(id === current));
            tab.addEventListener("click", () => {
                current = id;
                tabs.querySelectorAll(".auth-tab").forEach((t, i) => t.setAttribute("aria-selected", String(TABS[i][0] === id)));
                refresh();
            });
            tabs.append(tab);
        });
        dialog.replaceChildren(head, tabs, el("div", "adm-body"));
        if (!dialog.open) dialog.showModal();
        refresh();
    }

    ICC.admin = { open };
})();
