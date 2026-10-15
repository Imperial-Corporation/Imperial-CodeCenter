"use strict";

/*
 * Affichage d'une réponse structurée (synthèse, sections, code, sources).
 *
 * Tout le contenu — celui de l'utilisateur comme celui du modèle — est
 * inséré avec textContent : aucun HTML généré n'est jamais interprété.
 * `ICC.answerView.render(response)` retourne un élément autonome, que la
 * future chatbox pourra afficher dans chaque message.
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    /* Texte avec `code` en ligne, sans innerHTML. */
    function inlineText(parent, text) {
        String(text).split(/(`[^`\n]+`)/g).forEach((part) => {
            if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) {
                parent.append(el("code", "ai-inline-code", part.slice(1, -1)));
            } else if (part) {
                parent.append(document.createTextNode(part));
            }
        });
    }

    /* Texte du modèle : Markdown rendu en nœuds DOM sûrs (voir markdown.js). */
    function richText(text, className, sourceIds, idPrefix) {
        const box = el("div", className);
        if (ICC.markdown) {
            box.append(ICC.markdown.render(text, { sourceIds, idPrefix }));
        } else {
            String(text || "").split(/\n{2,}/).filter((c) => c.trim()).forEach((chunk) => {
                const p = el("p");
                inlineText(p, chunk.trim());
                box.append(p);
            });
        }
        return box;
    }

    function badge(text, kind, title) {
        const node = el("span", `ai-badge ai-badge-${kind}`, text);
        if (title) node.title = title;
        return node;
    }

    function copyButton(getText) {
        const button = el("button", "ai-copy", "Copier");
        button.type = "button";

        button.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(getText());
                button.textContent = "Copié";
            } catch {
                button.textContent = "Échec";
            }
            window.setTimeout(() => { button.textContent = "Copier"; }, 1500);
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
            head.append(badge("Généré, non vérifié", "warn", "Code écrit par le modèle, sans source."));
        }

        head.append(copyButton(() => block.code));

        const pre = el("pre");
        const code = el("code", "", block.code);
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
        head.append(el("span", "ai-source-id", source.id));
        head.append(el("span", "ai-source-path", source.path));
        card.append(head);

        const meta = [source.language_name || source.language, `score ${Number(source.score).toFixed(2)}`];
        if (source.categories && source.categories.length) meta.push(source.categories.join(" / "));
        card.append(el("p", "ai-source-meta", meta.filter(Boolean).join(" · ")));

        if (source.summary) card.append(el("p", "ai-source-summary", source.summary));

        (source.excerpts || []).slice(0, 3).forEach((excerpt) => {
            const details = el("details", "ai-excerpt");
            details.append(el("summary", "", `Extrait · lignes ${excerpt.line_start}-${excerpt.line_end}`));
            const pre = el("pre");
            pre.append(el("code", "", excerpt.text));
            details.append(pre);
            card.append(details);
        });

        return card;
    }

    function sourcesSection(sources, idPrefix) {
        const section = el("section", "ai-sources");
        const cited = sources.filter((s) => s.cited);
        const others = sources.filter((s) => !s.cited);

        if (cited.length > 0) {
            section.append(el("h3", "ai-h", `Sources utilisées (${cited.length})`));
            cited.forEach((s) => section.append(sourceCard(s, idPrefix)));
        }

        if (others.length > 0) {
            const details = el("details", "ai-other-sources");
            details.open = cited.length === 0;
            details.append(el("summary", "", cited.length
                ? `Autres résultats (${others.length})`
                : `Résultats de l'index (${others.length})`));
            others.forEach((s) => details.append(sourceCard(s, idPrefix)));
            section.append(details);
        }

        return section;
    }

    function followUpChips(items, onFollowUp) {
        const box = el("section", "ai-followups");
        box.append(el("h3", "ai-h", "Aller plus loin"));
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

    function toolTrace(trace) {
        const details = el("details", "ai-trace");
        details.append(el("summary", "", `Outils utilisés (${trace.length})`));

        const list = el("ul");
        trace.forEach((call) => {
            const args = Object.entries(call.arguments || {})
                .map(([key, value]) => `${key}=${value}`)
                .join(", ");
            const item = el("li", call.ok ? "" : "ai-trace-error", `${call.name}(${args}) — ${call.ok ? "ok" : call.error || "erreur"}`);
            list.append(item);
        });

        details.append(list);
        return details;
    }

    function render(response, options = {}) {
        const root = el("div", "ai-answer");
        root.dataset.status = response.status;
        const sourceIds = (response.sources || []).map((s) => s.id);

        if (Array.isArray(response.warnings) && response.warnings.length > 0) {
            const box = el("ul", "ai-warnings");
            box.setAttribute("role", "status");
            response.warnings.forEach((w) => box.append(el("li", "", w)));
            root.append(box);
        }

        if (response.status === "no_results") {
            root.append(el("p", "ai-empty", "Aucun résultat dans l'index pour cette recherche."));
        }

        if (response.answer) {
            const summary = el("section", "ai-summary");
            summary.append(el("h3", "ai-h", "Synthèse"));
            summary.append(richText(response.answer.summary, "ai-text", sourceIds, options.idPrefix));
            root.append(summary);

            response.answer.sections.forEach((section) => {
                const box = el("section", "ai-section");
                if (section.title) box.append(el("h3", "ai-h", section.title));
                if (section.body) box.append(richText(section.body, "ai-text", sourceIds, options.idPrefix));
                section.code_blocks.forEach((block) => box.append(codeBlock(block)));
                root.append(box);
            });

            if (response.answer.model_notes.length > 0) {
                const notes = el("ul", "ai-model-notes");
                response.answer.model_notes.forEach((n) => notes.append(el("li", "", `Note du modèle : ${n}`)));
                root.append(notes);
            }
        }

        const followUps = (response.answer && response.answer.follow_ups) || [];
        if (followUps.length > 0 && options.onFollowUp) root.append(followUpChips(followUps, options.onFollowUp));

        if (response.sources.length > 0) root.append(sourcesSection(response.sources, options.idPrefix));
        if (response.tool_trace && response.tool_trace.length > 0) root.append(toolTrace(response.tool_trace));
        root.append(metaLine(response));

        return root;
    }

    ICC.answerView = { render };
})();
