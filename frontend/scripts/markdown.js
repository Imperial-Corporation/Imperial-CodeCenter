// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Rendu Markdown SÛR : le texte est analysé puis converti en nœuds DOM avec
 * textContent. Aucun innerHTML, donc aucun HTML du modèle n'est jamais
 * interprété (une balise <script> s'affiche comme du texte).
 *
 * Pris en charge : titres (#…####), paragraphes, **gras**, *italique*,
 * ~~barré~~, `code`, blocs ``` (avec bouton Copier), listes ordonnées et à
 * puces (imbriquées par indentation), citations, séparateurs, tableaux,
 * liens http(s)/mailto (rel="noopener noreferrer", nouvel onglet) et
 * références de sources [S1] (liens vers la fiche si elle existe).
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});
    const MAX_LIST_DEPTH = 4;
    const SAFE_LINK = /^(https?:\/\/|mailto:)/i;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    /* ---------- éléments en ligne ---------- */

    // Ordre : code d'abord (son contenu n'est jamais interprété), puis liens,
    // puis références de sources, puis emphases.
    const INLINE = /(`[^`\n]+`)|(\[([^\]\n]{1,200})\]\(([^)\s]{1,2000})\))|(\[(S\d{1,3})\])|(\*\*([^*\n]+)\*\*)|(~~([^~\n]+)~~)|(\*([^*\s][^*\n]*)\*)|(__([^_\n]+)__)/g;

    function inline(parent, text, context) {
        let last = 0;
        // Expression locale : l'appel est récursif (liens, gras…), un lastIndex partagé bouclerait à l'infini.
        const pattern = new RegExp(INLINE.source, "g");
        let match;

        while ((match = pattern.exec(text)) !== null) {
            if (match.index > last) parent.append(document.createTextNode(text.slice(last, match.index)));
            last = match.index + match[0].length;

            if (match[1]) {
                parent.append(el("code", "ai-inline-code", match[1].slice(1, -1)));
            } else if (match[2]) {
                const url = match[4];
                if (SAFE_LINK.test(url)) {
                    const link = el("a", "ai-link");
                    link.href = url;
                    link.target = "_blank";
                    link.rel = "noopener noreferrer";
                    inline(link, match[3], context);
                    parent.append(link);
                } else {
                    // Lien dangereux (javascript:, data:…) : seul le texte est conservé.
                    parent.append(document.createTextNode(match[3]));
                }
            } else if (match[5]) {
                const id = match[6];
                if (context && context.sourceIds && context.sourceIds.has(id)) {
                    const ref = el("a", "ai-ref", id);
                    ref.href = `#${(context.idPrefix || "")}source-${id}`;
                    ref.title = `Voir la source ${id}`;
                    parent.append(ref);
                } else {
                    parent.append(document.createTextNode(match[5]));
                }
            } else if (match[7] || match[13]) {
                const strong = el("strong");
                inline(strong, match[8] || match[14], context);
                parent.append(strong);
            } else if (match[9]) {
                const del = el("del");
                inline(del, match[10], context);
                parent.append(del);
            } else if (match[11]) {
                const em = el("em");
                inline(em, match[12], context);
                parent.append(em);
            }
        }

        if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
    }

    /* ---------- blocs ---------- */

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

    function fencedCode(language, code) {
        const figure = el("figure", "ai-code ai-md-code");
        const head = el("div", "ai-code-head");
        head.append(el("span", "ai-code-lang", language || "texte"), copyButton(() => code));
        const pre = el("pre");
        pre.tabIndex = 0;
        const codeNode = el("code");
        if (ICC.highlight) codeNode.append(ICC.highlight.render(code, language));
        else codeNode.textContent = code;
        pre.append(codeNode);
        figure.append(head, pre);
        return figure;
    }

    const HEADING = /^(#{1,4})\s+(.+?)\s*#*\s*$/;
    const FENCE = /^```\s*([A-Za-z0-9_+#.\-]{0,30})\s*$/;
    const HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
    const LIST_ITEM = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/;
    const QUOTE = /^\s{0,3}>\s?(.*)$/;
    const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

    function splitRow(line) {
        let row = line.trim();
        if (row.startsWith("|")) row = row.slice(1);
        if (row.endsWith("|")) row = row.slice(0, -1);
        return row.split("|").map((cell) => cell.trim());
    }

    function parseList(lines, start, context, depth) {
        const first = LIST_ITEM.exec(lines[start]);
        const baseIndent = first[1].length;
        const ordered = /\d/.test(first[2]);
        const list = el(ordered ? "ol" : "ul", "ai-md-list");
        let i = start;

        while (i < lines.length) {
            const match = LIST_ITEM.exec(lines[i]);
            if (!match) break;
            const indent = match[1].length;

            if (indent < baseIndent) break;

            if (indent > baseIndent) {
                if (depth >= MAX_LIST_DEPTH) {
                    i++;
                    continue;
                }
                const [sub, next] = parseList(lines, i, context, depth + 1);
                const holder = list.lastElementChild || list.appendChild(el("li"));
                holder.append(sub);
                i = next;
                continue;
            }

            const item = el("li");
            inline(item, match[3], context);
            list.append(item);
            i++;

            // Suite d'un élément : lignes indentées qui ne sont pas des puces.
            while (i < lines.length && lines[i].trim() && !LIST_ITEM.test(lines[i]) && /^\s{2,}/.test(lines[i])) {
                item.append(document.createElement("br"));
                inline(item, lines[i].trim(), context);
                i++;
            }
        }
        return [list, i];
    }

    function render(text, options) {
        const context = {
            sourceIds: options && options.sourceIds ? new Set(options.sourceIds) : null,
            idPrefix: (options && options.idPrefix) || "",
        };
        const root = el("div", "ai-md");
        const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
        let i = 0;

        while (i < lines.length) {
            const line = lines[i];

            if (!line.trim()) { i++; continue; }

            const fence = FENCE.exec(line);
            if (fence) {
                const code = [];
                i++;
                while (i < lines.length && !/^```\s*$/.test(lines[i])) code.push(lines[i++]);
                i++; // clôture
                root.append(fencedCode(fence[1], code.join("\n")));
                continue;
            }

            const heading = HEADING.exec(line);
            if (heading) {
                // Le titre de section de la page est un h3 : on décale pour garder la hiérarchie.
                const level = Math.min(6, heading[1].length + 3);
                const node = el(`h${level}`, "ai-md-h");
                inline(node, heading[2], context);
                root.append(node);
                i++;
                continue;
            }

            if (HR.test(line)) { root.append(el("hr", "ai-md-hr")); i++; continue; }

            if (QUOTE.test(line)) {
                const quoted = [];
                while (i < lines.length && QUOTE.test(lines[i])) quoted.push(QUOTE.exec(lines[i++])[1]);
                const block = el("blockquote", "ai-md-quote");
                block.append(render(quoted.join("\n"), options));
                root.append(block);
                continue;
            }

            if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
                const header = splitRow(line);
                i += 2;
                const wrap = el("div", "ai-md-table-wrap");
                const table = el("table", "ai-md-table");
                const thead = el("thead");
                const hr = el("tr");
                header.forEach((cell) => { const th = el("th"); inline(th, cell, context); hr.append(th); });
                thead.append(hr);
                const tbody = el("tbody");
                let rows = 0;
                while (i < lines.length && lines[i].includes("|") && lines[i].trim() && rows < 200) {
                    const tr = el("tr");
                    splitRow(lines[i]).slice(0, header.length).forEach((cell) => {
                        const td = el("td"); inline(td, cell, context); tr.append(td);
                    });
                    tbody.append(tr);
                    i++; rows++;
                }
                table.append(thead, tbody);
                wrap.append(table);
                root.append(wrap);
                continue;
            }

            if (LIST_ITEM.test(line)) {
                const [list, next] = parseList(lines, i, context, 1);
                root.append(list);
                i = next;
                continue;
            }

            // Paragraphe : jusqu'à la prochaine ligne vide ou le prochain bloc.
            const paragraph = [];
            while (
                i < lines.length && lines[i].trim() &&
                !FENCE.test(lines[i]) && !HEADING.test(lines[i]) && !HR.test(lines[i]) &&
                !QUOTE.test(lines[i]) && !LIST_ITEM.test(lines[i])
            ) {
                paragraph.push(lines[i++]);
            }
            const p = el("p");
            paragraph.forEach((row, index) => {
                if (index > 0) p.append(document.createElement("br"));
                inline(p, row, context);
            });
            root.append(p);
        }

        return root;
    }

    ICC.markdown = { render };
})();
