// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Affichage d'une réponse structurée : une carte par réponse, avec des blocs
 * séparés (synthèse, sections, code coloré), puis sources repliées, pistes
 * « pour aller plus loin » et détails techniques repliés.
 *
 * Tout le contenu — celui de l'utilisateur comme celui du modèle — est inséré
 * avec textContent : aucun HTML généré n'est jamais interprété.
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    /* Texte du modèle : Markdown rendu en nœuds DOM sûrs (voir markdown.js). */
    function richText(text, className, sourceIds, idPrefix) {
        const box = el("div", className);
        if (ICC.markdown) {
            box.append(ICC.markdown.render(text, { sourceIds, idPrefix }));
        } else {
            String(text || "").split(/\n{2,}/).filter((c) => c.trim()).forEach((chunk) => {
                box.append(el("p", "", chunk.trim()));
            });
        }
        return box;
    }

    function badge(text, kind, title) {
        const node = el("span", `ai-badge ai-badge-${kind}`, text);
        if (title) node.title = title;
        return node;
    }

    function copyButton(getText, label = "Copier") {
        const button = el("button", "ai-copy", label);
        button.type = "button";

        button.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(getText());
                button.textContent = "Copié";
            } catch {
                button.textContent = "Échec";
            }
            window.setTimeout(() => { button.textContent = label; }, 1500);
        });

        return button;
    }

    function codeBlock(block) {
        const wrapper = el("figure", "ai-code");
        const head = el("div", "ai-code-head");

        head.append(el("span", "ai-code-lang", block.language || "texte"));

        if (block.verified) {
            head.append(badge(
                `Extrait de ${block.source_ids.join(", ")}`, "ok",
                "Ce code figure littéralement dans la source citée.",
            ));
        } else if (block.source_ids.length > 0) {
            head.append(badge(
                `Adapté de ${block.source_ids.join(", ")}`, "warn",
                "Ce code n'a pas été retrouvé à l'identique dans la source citée.",
            ));
        } else {
            head.append(badge("Non vérifié", "warn", "Code écrit par le modèle, sans source."));
        }

        head.append(copyButton(() => block.code));

        const pre = el("pre");
        const code = el("code");
        if (ICC.highlight) code.append(ICC.highlight.render(block.code, block.language));
        else code.textContent = block.code;
        if (block.language) code.dataset.language = block.language;
        pre.tabIndex = 0;
        pre.append(code);

        wrapper.append(head, pre);
        if (block.caption) wrapper.append(el("figcaption", "ai-code-caption", block.caption));
        return wrapper;
    }

    function sourceCard(source, idPrefix) {
        const card = el("article", "ai-source");
        card.id = `${idPrefix || ""}source-${source.id}`;
        card.dataset.documentId = source.document_id;

        const head = el("div", "ai-source-head");
        head.append(el("span", "ai-source-id", source.id), el("span", "ai-source-path", source.path));
        card.append(head);

        const meta = [source.language_name || source.language];
        if (source.categories && source.categories.length) meta.push(source.categories.join(" / "));
        card.append(el("p", "ai-source-meta", meta.filter(Boolean).join(" · ")));

        if (source.summary) card.append(el("p", "ai-source-summary", source.summary));

        (source.excerpts || []).slice(0, 3).forEach((excerpt) => {
            const details = el("details", "ai-excerpt");
            details.append(el("summary", "", `Extrait · lignes ${excerpt.line_start}-${excerpt.line_end}`));
            const pre = el("pre");
            const code = el("code");
            if (ICC.highlight) code.append(ICC.highlight.render(excerpt.text, source.language));
            else code.textContent = excerpt.text;
            pre.append(code);
            details.append(pre);
            card.append(details);
        });

        return card;
    }

    function sourcesSection(sources, idPrefix) {
        const cited = sources.filter((s) => s.cited);
        const details = el("details", "ai-sources");
        details.open = cited.length === 0 && sources.length <= 3;

        const summary = el("summary", "ai-sources-summary");
        summary.append(el("span", "ai-sources-title",
            cited.length ? `Sources utilisées · ${cited.length}` : `Résultats de l'index · ${sources.length}`));
        cited.slice(0, 4).forEach((s) => summary.append(el("span", "ai-source-chip", `${s.id} ${s.path.split("/").pop()}`)));
        details.append(summary);

        const list = el("div", "ai-sources-list");
        cited.forEach((s) => list.append(sourceCard(s, idPrefix)));
        const others = sources.filter((s) => !s.cited);
        if (others.length > 0) {
            const more = el("details", "ai-other-sources");
            more.open = cited.length === 0;
            more.append(el("summary", "", cited.length ? `Autres résultats (${others.length})` : "Détails des résultats"));
            others.forEach((s) => more.append(sourceCard(s, idPrefix)));
            list.append(more);
        }
        details.append(list);
        return details;
    }

    function followUpChips(items, onFollowUp) {
        const box = el("section", "ai-followups");
        box.append(el("h4", "ai-h", "Pour aller plus loin"));
        const row = el("div", "ai-followup-row");
        items.slice(0, 4).forEach((text) => {
            const chip = el("button", "ai-chip", text);
            chip.type = "button";
            chip.addEventListener("click", () => onFollowUp(text));
            row.append(chip);
        });
        box.append(row);
        return box;
    }

    function metaLine(response) {
        const meta = response.meta || {};
        const parts = [];

        if (meta.provider) parts.push(`${meta.provider}${meta.model ? ` · ${meta.model}` : ""}`);
        const depthLabel = { overview: "aperçu", focused: "ciblé", deep: "approfondi" }[meta.depth];
        if (depthLabel) parts.push(`mode ${depthLabel}`);
        if (meta.tool_calls) parts.push(`${meta.tool_calls} appel${meta.tool_calls > 1 ? "s" : ""} d'outil`);
        if (meta.sources_compressed) parts.push(`${meta.sources_compressed} fichier(s) compressé(s)`);
        if (Number.isFinite(meta.took_ms)) parts.push(`${(meta.took_ms / 1000).toFixed(1)} s`);

        return el("p", "ai-meta", parts.join(" · "));
    }

    function technical(response) {
        const details = el("details", "ai-trace");
        details.append(el("summary", "", "Détails techniques"));
        details.append(metaLine(response));

        const trace = response.tool_trace || [];
        if (trace.length > 0) {
            const list = el("ul");
            trace.forEach((call) => {
                const args = Object.entries(call.arguments || {}).map(([key, value]) => `${key}=${value}`).join(", ");
                list.append(el("li", call.ok ? "" : "ai-trace-error",
                    `${call.name}(${args}) — ${call.ok ? "ok" : call.error || "erreur"}`));
            });
            details.append(list);
        }
        return details;
    }

    function warningsBox(warnings) {
        const details = el("details", "ai-warnings");
        details.setAttribute("role", "status");
        details.append(el("summary", "", `${warnings.length} remarque${warnings.length > 1 ? "s" : ""} sur cette réponse`));
        const list = el("ul");
        warnings.forEach((w) => list.append(el("li", "", w)));
        details.append(list);
        return details;
    }

    function plainText(response) {
        const answer = response.answer;
        if (!answer) return "";
        const parts = [answer.summary];
        answer.sections.forEach((s) => {
            if (s.title) parts.push(`### ${s.title}`);
            if (s.body) parts.push(s.body);
            s.code_blocks.forEach((b) => parts.push("```" + (b.language || "") + "\n" + b.code + "\n```"));
        });
        return parts.join("\n\n");
    }

    function render(response, options = {}) {
        const root = el("article", "ai-answer ai-card");
        root.dataset.status = response.status;
        const sourceIds = (response.sources || []).map((s) => s.id);
        const body = el("div", "ai-card-body");
        root.append(body);

        if (response.status === "no_results") {
            body.append(el("p", "ai-empty", "Aucun résultat dans l'index pour cette recherche."));
        }

        if (response.answer) {
            const summary = el("section", "ai-block ai-summary");
            summary.append(richText(response.answer.summary, "ai-text", sourceIds, options.idPrefix));
            body.append(summary);

            response.answer.sections.forEach((section) => {
                const box = el("section", "ai-block ai-section");
                if (section.title) box.append(el("h4", "ai-h", section.title));
                if (section.body) box.append(richText(section.body, "ai-text", sourceIds, options.idPrefix));
                section.code_blocks.forEach((block) => box.append(codeBlock(block)));
                body.append(box);
            });

            if (response.answer.model_notes.length > 0) {
                const notes = el("ul", "ai-model-notes");
                response.answer.model_notes.forEach((n) => notes.append(el("li", "", `Note du modèle : ${n}`)));
                body.append(notes);
            }
        }

        if (Array.isArray(response.warnings) && response.warnings.length > 0) {
            body.append(warningsBox(response.warnings));
        }

        const foot = el("div", "ai-card-foot");
        const sources = response.sources || [];
        let sourcesNode = null;
        if (sources.length > 0) {
            sourcesNode = sourcesSection(sources, options.idPrefix);
            foot.append(sourcesNode);
        }

        const followUps = (response.answer && response.answer.follow_ups) || [];
        if (followUps.length > 0 && options.onFollowUp) foot.append(followUpChips(followUps, options.onFollowUp));

        const actions = el("div", "ai-actions");
        if (response.answer) actions.append(copyButton(() => plainText(response), "Copier la réponse"));
        if (response.answer && options.onDeepen) {
            const deepen = el("button", "ai-action", "Approfondir");
            deepen.type = "button";
            deepen.title = "Demande à l'IA de détailler et de chercher des sources plus spécialisées";
            deepen.addEventListener("click", () => options.onDeepen());
            actions.append(deepen);
        }
        foot.append(actions);
        foot.append(technical(response));
        root.append(foot);

        // Un clic sur une référence [S1] ouvre la liste des sources et met la fiche en évidence.
        root.addEventListener("click", (event) => {
            const ref = event.target.closest && event.target.closest("a.ai-ref");
            if (!ref || !sourcesNode) return;
            event.preventDefault();
            sourcesNode.open = true;
            const target = root.querySelector(`[id="${ref.getAttribute("href").slice(1)}"]`);
            if (target) {
                target.scrollIntoView({ block: "center", behavior: "smooth" });
                target.classList.add("is-flash");
                window.setTimeout(() => target.classList.remove("is-flash"), 1600);
            }
        });

        return root;
    }

    ICC.answerView = { render, plainText };
})();
