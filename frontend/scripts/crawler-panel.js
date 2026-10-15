"use strict";

/*
 * Panneau « Crawler GitHub » (bouton à côté de l'engrenage).
 *
 * Permet de choisir QUOI collecter, QUAND démarrer (maintenant ou à une
 * heure précise) et QUAND s'arrêter : à une heure donnée, après N minutes,
 * après N Go téléchargés ou quand la bibliothèque atteint N Go. Une garde
 * disque (espace libre minimal) est toujours active côté serveur.
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});

    const LANGUAGES = [
        ["python", "Python"], ["javascript", "JavaScript"], ["typescript", "TypeScript"], ["rust", "Rust"],
        ["go", "Go"], ["c", "C"], ["cpp", "C++"], ["java", "Java"], ["csharp", "C#"], ["php", "PHP"],
        ["ruby", "Ruby"], ["shell", "Shell"], ["sql", "SQL"], ["kotlin", "Kotlin"], ["swift", "Swift"],
        ["lua", "Lua"], ["zig", "Zig"], ["scala", "Scala"], ["html", "HTML"], ["css", "CSS"],
    ];

    const STATE_LABELS = {
        scheduled: "Planifié", running: "En cours", stopping: "Arrêt en cours", finished: "Terminé",
        stopped: "Arrêté", failed: "Échec", cancelled: "Annulé", interrupted: "Interrompu", missed: "Manqué",
    };

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
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

    function numberInput(value, min, max, step) {
        const input = el("input");
        input.type = "number";
        input.min = String(min);
        input.max = String(max);
        input.step = String(step || 1);
        input.value = String(value);
        return input;
    }

    function formatBytes(bytes) {
        if (!Number.isFinite(bytes)) return "—";
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} Ko`;
        if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} Mo`;
        return `${(bytes / 1024 ** 3).toFixed(2)} Go`;
    }

    function formatDuration(seconds) {
        if (!Number.isFinite(seconds)) return "—";
        const s = Math.round(seconds);
        if (s < 60) return `${s} s`;
        const m = Math.floor(s / 60);
        return m < 60 ? `${m} min ${s % 60} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
    }

    function formatDate(iso) {
        if (!iso) return "—";
        const date = new Date(iso);
        return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("fr-FR");
    }

    // <input type="datetime-local"> donne une heure locale du navigateur : on la
    // convertit en instant absolu (UTC) pour que le serveur ne se trompe pas de fuseau.
    function toUtcIso(localValue) {
        if (!localValue) return null;
        const date = new Date(localValue);
        return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }

    function init() {
        const services = ICC.services;
        if (!services) return;

        let busy = false;
        let timer = null;
        let lastStatus = null;

        /* ----- bouton flottant (à côté de l'engrenage) ----- */

        const fab = el("button", "settings-fab crawler-fab");
        fab.type = "button";
        fab.title = "Crawler GitHub";
        fab.setAttribute("aria-label", "Ouvrir le crawler GitHub");
        fab.setAttribute("aria-haspopup", "dialog");
        fab.setAttribute("aria-expanded", "false");
        fab.innerHTML =
            '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" ' +
            'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="9"></circle><path d="M3 12h18"></path>' +
            '<path d="M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z"></path></svg>';

        const panel = el("section", "settings-panel crawler-panel");
        panel.id = "crawler-panel";
        panel.hidden = true;
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-labelledby", "cp-title");
        fab.setAttribute("aria-controls", panel.id);

        const head = el("div", "sp-head");
        const title = el("h2", "", "Crawler GitHub");
        title.id = "cp-title";
        const close = el("button", "sp-close", "×");
        close.type = "button";
        close.setAttribute("aria-label", "Fermer");
        head.append(title, close);

        const intro = el("p", "sp-intro",
            "Collecte automatiquement des scripts sous licence permissive depuis GitHub et les range par catégorie. " +
            "Choisis quand lancer et quand arrêter : le premier objectif atteint stoppe la collecte proprement.");

        /* ----- état de la collecte ----- */

        const statusCard = el("div", "cp-status");
        statusCard.setAttribute("aria-live", "polite");

        /* ----- formulaire ----- */

        const form = el("form", "sp-body");
        form.noValidate = true;

        const langBox = el("div", "cp-langs");
        const langInputs = new Map();
        LANGUAGES.forEach(([slug, label]) => {
            const wrap = el("label", "cp-lang");
            const input = el("input");
            input.type = "checkbox";
            input.value = slug;
            input.checked = slug === "python";
            langInputs.set(slug, input);
            wrap.append(input, document.createTextNode(` ${label}`));
            langBox.append(wrap);
        });
        const langField = el("div", "sp-field");
        langField.append(el("span", "sp-label", "Langages à collecter"), langBox);

        const queryInput = el("input");
        queryInput.type = "text";
        queryInput.placeholder = "ex. api rest, algorithms, cli";
        queryInput.maxLength = 80;
        const queryField = field("cp-query", "Mots-clés (facultatif)", queryInput,
            "Sans mot-clé : les dépôts les plus étoilés du langage.");

        const starsInput = numberInput(100, 0, 1000000);
        const reposInput = numberInput(20, 1, 500);
        const perRepoInput = numberInput(15, 1, 200);
        const maxFilesInput = numberInput(200, 1, 20000);
        const topicsInput = el("input");
        topicsInput.type = "text";
        topicsInput.placeholder = "ex. fastapi, cli";
        const reposListInput = el("input");
        reposListInput.type = "text";
        reposListInput.placeholder = "ex. pallets/flask, tiangolo/fastapi";

        const advancedSearch = el("details", "sp-advanced");
        advancedSearch.append(el("summary", "", "Réglages de collecte"));
        const advGrid = el("div", "sp-advanced-grid");
        advGrid.append(
            field("cp-stars", "Étoiles minimum", starsInput),
            field("cp-repos", "Dépôts maximum", reposInput),
            field("cp-perrepo", "Fichiers par dépôt", perRepoInput),
            field("cp-maxfiles", "Fichiers au total", maxFilesInput),
            field("cp-topics", "Topics GitHub", topicsInput, "Séparés par des virgules."),
            field("cp-repolist", "Dépôts précis", reposListInput, "proprietaire/nom, séparés par des virgules."),
        );
        advancedSearch.append(advGrid);

        /* quand démarrer */
        const startNow = el("input");
        startNow.type = "radio"; startNow.name = "cp-start"; startNow.value = "now"; startNow.checked = true;
        const startLater = el("input");
        startLater.type = "radio"; startLater.name = "cp-start"; startLater.value = "later";
        const startAt = el("input");
        startAt.type = "datetime-local";
        startAt.disabled = true;
        const startBox = el("div", "sp-field");
        startBox.append(el("span", "sp-label", "Quand démarrer"));
        const startRow1 = el("label", "sp-check");
        startRow1.append(startNow, document.createTextNode(" Maintenant"));
        const startRow2 = el("label", "sp-check");
        startRow2.append(startLater, document.createTextNode(" À une heure précise"), startAt);
        startBox.append(startRow1, startRow2);

        function toggleStart() { startAt.disabled = !startLater.checked; if (startLater.checked) startAt.focus(); }
        startNow.addEventListener("change", toggleStart);
        startLater.addEventListener("change", toggleStart);

        /* quand s'arrêter */
        function stopRow(label, control, unit) {
            const wrap = el("label", "cp-stop");
            const check = el("input");
            check.type = "checkbox";
            control.disabled = true;
            check.addEventListener("change", () => { control.disabled = !check.checked; if (check.checked) control.focus(); });
            wrap.append(check, document.createTextNode(` ${label} `), control);
            if (unit) wrap.append(document.createTextNode(` ${unit}`));
            return { wrap, check, control };
        }
        const stopAtInput = el("input");
        stopAtInput.type = "datetime-local";
        const rowAt = stopRow("S'arrêter à", stopAtInput);
        const rowMinutes = stopRow("S'arrêter après", numberInput(60, 1, 10080), "minutes");
        const rowGb = stopRow("S'arrêter après", numberInput(1, 0.01, 10000, 0.01), "Go téléchargés");
        const rowLibrary = stopRow("S'arrêter quand la bibliothèque atteint", numberInput(5, 0.01, 100000, 0.01), "Go");
        const stopBox = el("div", "sp-field");
        stopBox.append(el("span", "sp-label", "Quand s'arrêter (le premier objectif atteint gagne)"),
            rowAt.wrap, rowMinutes.wrap, rowGb.wrap, rowLibrary.wrap);

        const freeInput = numberInput(2, 0.5, 10000, 0.5);
        const freeField = field("cp-free", "Sécurité disque : garder au moins (Go libres)", freeInput,
            "La collecte s'arrête d'elle-même si l'espace libre passe sous ce seuil.");

        /* jeton GitHub */
        const tokenInput = el("input");
        tokenInput.type = "password";
        tokenInput.autocomplete = "new-password";
        tokenInput.placeholder = "ghp_… ou github_pat_…";
        const tokenSave = el("button", "sp-button", "Enregistrer le jeton");
        tokenSave.type = "button";
        const tokenClear = el("button", "sp-button", "Effacer");
        tokenClear.type = "button";
        const tokenField = field("cp-token", "Jeton GitHub (facultatif)", tokenInput,
            "Sans jeton : 60 requêtes/h seulement. Stocké chiffré, jamais renvoyé au navigateur.");
        const tokenActions = el("div", "sp-actions");
        tokenActions.append(tokenSave, tokenClear);
        tokenField.append(tokenActions);

        const adminInput = el("input");
        adminInput.type = "password";
        adminInput.autocomplete = "off";
        adminInput.value = services.adminToken.get();
        const adminField = field("cp-admin", "Jeton d'administration", adminInput,
            "Requis par ce serveur. Gardé pour cet onglet seulement.");
        adminField.hidden = true;

        const actions = el("div", "sp-actions");
        const startButton = el("button", "sp-button sp-primary", "Lancer la collecte");
        startButton.type = "submit";
        const stopButton = el("button", "sp-button", "Arrêter");
        stopButton.type = "button";
        actions.append(startButton, stopButton);

        const message = el("p", "sp-status");
        message.setAttribute("role", "status");
        message.setAttribute("aria-live", "polite");

        form.append(langField, queryField, advancedSearch, startBox, stopBox, freeField, tokenField, adminField, actions, message);
        panel.append(head, intro, statusCard, form);
        document.body.append(fab, panel);

        /* ----- affichage ----- */

        function setMessage(text, kind) {
            message.textContent = text || "";
            message.dataset.kind = kind || "";
        }

        function renderStatus(status) {
            lastStatus = status;
            statusCard.replaceChildren();
            const job = status.job;
            const disk = status.disk || {};
            const info = [];
            if (Number.isFinite(disk.free_gb)) info.push(`Disque : ${disk.free_gb} Go libres sur ${disk.total_gb} Go`);
            info.push(`Jeton GitHub : ${({ enregistre: "enregistré", environnement: "variable d'environnement", aucun: "aucun" })[status.token] || "—"}`);
            statusCard.append(el("p", "cp-info", info.join(" · ")));

            if (!status.available) {
                statusCard.append(el("p", "cp-warn", "Le module du crawler est introuvable sur ce serveur."));
            }

            if (job) {
                const box = el("div", "cp-job");
                box.dataset.state = job.state;
                const top = el("div", "cp-job-head");
                top.append(el("strong", "", STATE_LABELS[job.state] || job.state));
                if (job.state === "scheduled" && job.start_at) top.append(el("span", "", `départ ${formatDate(job.start_at)}`));
                box.append(top);
                const progress = job.progress || {};
                if (job.state === "running" || job.state === "stopping" || job.ended_at) {
                    box.append(el("p", "cp-progress",
                        `${progress.files ?? 0} fichier(s) · ${formatBytes(progress.bytes)} · ${progress.repos ?? 0} dépôt(s) · ` +
                        `${progress.skipped ?? 0} ignoré(s) · ${formatDuration(progress.elapsed)}`));
                }
                if (job.message) box.append(el("p", "cp-message", job.message));
                if (job.log && job.log.length) {
                    const details = el("details", "cp-log");
                    details.append(el("summary", "", "Journal"));
                    details.append(el("pre", "", job.log.join("\n")));
                    box.append(details);
                }
                statusCard.append(box);
            }

            const active = !!status.active;
            startButton.disabled = busy || active || !status.available;
            stopButton.disabled = busy || !active;
            stopButton.textContent = job && job.state === "scheduled" ? "Annuler la planification" : "Arrêter";
        }

        async function refresh() {
            try {
                renderStatus(await services.crawlerStatus());
                adminField.hidden = true;
                setMessage("");
            } catch (error) {
                handleError(error);
            }
            schedulePoll();
        }

        function schedulePoll() {
            window.clearTimeout(timer);
            if (panel.hidden) return;
            const active = lastStatus && lastStatus.active;
            timer = window.setTimeout(refresh, active ? 3000 : 15000);
        }

        function handleError(error) {
            if (error.status === 401) {
                adminField.hidden = false;
                setMessage("Jeton d'administration requis ou invalide.", "error");
            } else {
                setMessage(error.message || "Erreur inattendue.", "error");
            }
        }

        function applyAdmin() { services.adminToken.set(adminInput.value.trim()); }

        function commaList(text) {
            return text.split(",").map((part) => part.trim()).filter(Boolean);
        }

        function buildRequest() {
            const languages = [...langInputs].filter(([, input]) => input.checked).map(([slug]) => slug);
            if (languages.length === 0) throw new Error("Choisis au moins un langage.");

            const request = {
                languages,
                query: queryInput.value.trim() || null,
                topics: commaList(topicsInput.value),
                repos: commaList(reposListInput.value),
                min_stars: Number(starsInput.value),
                max_repos: Number(reposInput.value),
                max_files_per_repo: Number(perRepoInput.value),
                max_files: Number(maxFilesInput.value),
                min_free_gb: Number(freeInput.value),
            };

            if (startLater.checked) {
                request.start_at = toUtcIso(startAt.value);
                if (!request.start_at) throw new Error("Indique l'heure de démarrage.");
                if (new Date(request.start_at).getTime() < Date.now() - 60000) {
                    throw new Error("L'heure de démarrage est déjà passée.");
                }
            }
            if (rowAt.check.checked) {
                request.stop_at = toUtcIso(rowAt.control.value);
                if (!request.stop_at) throw new Error("Indique l'heure d'arrêt.");
            }
            if (rowMinutes.check.checked) request.max_minutes = Number(rowMinutes.control.value);
            if (rowGb.check.checked) request.max_gb = Number(rowGb.control.value);
            if (rowLibrary.check.checked) request.max_library_gb = Number(rowLibrary.control.value);
            return request;
        }

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (busy) return;
            let request;
            try {
                request = buildRequest();
            } catch (error) {
                setMessage(error.message, "error");
                return;
            }
            applyAdmin();
            busy = true;
            startButton.disabled = true;
            setMessage("Démarrage…", "info");
            try {
                await services.crawlerStart(request);
                setMessage(request.start_at ? "Collecte planifiée." : "Collecte lancée.", "ok");
            } catch (error) {
                handleError(error);
            } finally {
                busy = false;
                await refresh();
            }
        });

        stopButton.addEventListener("click", async () => {
            if (busy) return;
            applyAdmin();
            busy = true;
            try {
                await services.crawlerStop();
                setMessage("Arrêt demandé : la collecte se termine proprement.", "ok");
            } catch (error) {
                handleError(error);
            } finally {
                busy = false;
                await refresh();
            }
        });

        async function saveToken(clear) {
            applyAdmin();
            try {
                await services.crawlerToken(clear ? null : tokenInput.value.trim(), clear);
                tokenInput.value = "";
                setMessage(clear ? "Jeton effacé." : "Jeton enregistré (chiffré).", "ok");
                await refresh();
            } catch (error) {
                handleError(error);
            }
        }
        tokenSave.addEventListener("click", () => saveToken(false));
        tokenClear.addEventListener("click", () => saveToken(true));

        /* ----- ouverture / fermeture ----- */

        function openPanel() {
            // Un seul panneau flottant à la fois.
            const other = document.getElementById("settings-panel");
            if (other && !other.hidden) document.querySelector(".settings-fab:not(.crawler-fab)").click();
            panel.hidden = false;
            fab.setAttribute("aria-expanded", "true");
            refresh();
        }

        function closePanel() {
            panel.hidden = true;
            window.clearTimeout(timer);
            fab.setAttribute("aria-expanded", "false");
            fab.focus();
        }

        fab.addEventListener("click", () => (panel.hidden ? openPanel() : closePanel()));
        close.addEventListener("click", closePanel);
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !panel.hidden) closePanel();
        });
        // Si le panneau de l'IA s'ouvre, celui-ci se ferme.
        document.addEventListener("click", (event) => {
            if (event.target.closest && event.target.closest(".settings-fab:not(.crawler-fab)") && !panel.hidden) {
                panel.hidden = true;
                fab.setAttribute("aria-expanded", "false");
            }
        });
    }

    document.addEventListener("DOMContentLoaded", init);
})();
