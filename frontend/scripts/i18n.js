// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Interface français / anglais.
 *
 * Le français est la langue source : tous les textes de l'interface sont écrits en français dans le
 * code. En anglais, ce module remplace — dans la page, y compris pour les éléments créés plus tard —
 * chaque texte dont la traduction figure dans `i18n-en.js` (correspondance exacte, ou motifs pour les
 * textes avec des nombres). Un texte sans traduction reste en français : rien ne casse.
 *
 * Langue : choix mémorisé (localStorage, facultatif), sinon celle du navigateur (français → français,
 * sinon anglais). Bouton FR | EN dans le pied de page.
 *
 * `ICC.i18n` : { lang, set, t }
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});
    const KEY = "icc-lang";
    const ATTRS = ["placeholder", "title", "aria-label", "alt"];
    const SKIP = new Set(["SCRIPT", "STYLE", "CODE", "PRE", "TEXTAREA", "SVG"]);

    function stored() {
        try { return window.localStorage.getItem(KEY); } catch { return null; }
    }

    function detect() {
        const saved = stored();
        if (saved === "fr" || saved === "en") return saved;
        const nav = (navigator.language || "fr").toLowerCase();
        return nav.startsWith("fr") ? "fr" : "en";
    }

    let lang = detect();
    // Dernière valeur écrite par ce module pour un nœud : si le code de l'application change le
    // texte ensuite, la nouvelle valeur est traitée comme un nouveau texte source (français).
    const written = new WeakMap();
    const source = new WeakMap();
    const attrWritten = new WeakMap();
    const attrSource = new WeakMap();
    let patterns = null;

    function dict() { return (ICC.i18nEn && ICC.i18nEn.exact) || {}; }

    function compilePatterns() {
        if (patterns) return patterns;
        patterns = ((ICC.i18nEn && ICC.i18nEn.patterns) || []).map(([re, out]) => [new RegExp(re), out]);
        return patterns;
    }

    function translate(text) {
        const exact = dict();
        const trimmed = text.trim();
        if (!trimmed) return text;
        let out = Object.prototype.hasOwnProperty.call(exact, trimmed) ? exact[trimmed] : null;
        if (out === null) {
            for (const [re, template] of compilePatterns()) {
                const m = re.exec(trimmed);
                if (m) { out = template.replace(/\$(\d)/g, (_, i) => (m[+i] === undefined ? "" : m[+i])); break; }
            }
        }
        if (out === null) return text;
        const lead = text.match(/^\s*/)[0];
        const tail = text.match(/\s*$/)[0];
        return lead + out + tail;
    }

    function t(frenchText) { return lang === "en" ? translate(String(frenchText)) : String(frenchText); }

    function applyText(node) {
        const current = node.data;
        let base = source.get(node);
        if (base === undefined || written.get(node) !== current) base = current;   // texte (re)défini par l'application
        source.set(node, base);
        const next = lang === "en" ? translate(base) : base;
        if (next !== current) node.data = next;
        written.set(node, next);
    }

    function applyAttrs(element) {
        for (const name of ATTRS) {
            if (!element.hasAttribute(name)) continue;
            const current = element.getAttribute(name);
            let map = attrSource.get(element);
            let wr = attrWritten.get(element);
            if (!map) { map = {}; attrSource.set(element, map); }
            if (!wr) { wr = {}; attrWritten.set(element, wr); }
            let base = map[name];
            if (base === undefined || wr[name] !== current) base = current;
            map[name] = base;
            const next = lang === "en" ? translate(base) : base;
            if (next !== current) element.setAttribute(name, next);
            wr[name] = next;
        }
    }

    function walk(root) {
        if (root.nodeType === Node.TEXT_NODE) {
            if (root.parentNode && !SKIP.has(root.parentNode.nodeName.toUpperCase())) applyText(root);
            return;
        }
        if (root.nodeType !== Node.ELEMENT_NODE || SKIP.has(root.nodeName.toUpperCase())) return;
        if (root.closest && root.closest("[data-no-i18n]")) return;
        applyAttrs(root);
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
            acceptNode(node) {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    if (node.hasAttribute("data-no-i18n")) return NodeFilter.FILTER_REJECT;
                    if (SKIP.has(node.nodeName.toUpperCase())) {
                        applyAttrs(node);   // le contenu reste intact, mais pas l'aide à la saisie (placeholder, title…)
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                }
                return NodeFilter.FILTER_ACCEPT;
            },
        });
        let node = walker.nextNode();
        while (node) {
            if (node.nodeType === Node.TEXT_NODE) applyText(node);
            else applyAttrs(node);
            node = walker.nextNode();
        }
    }

    let observer = null;
    let busy = false;

    function observe() {
        if (observer) return;
        observer = new MutationObserver((records) => {
            if (busy) return;
            busy = true;
            try {
                for (const record of records) {
                    if (record.type === "childList") record.addedNodes.forEach((n) => walk(n));
                    else if (record.type === "characterData") {
                        if (record.target.parentNode && !SKIP.has(record.target.parentNode.nodeName.toUpperCase())) applyText(record.target);
                    } else if (record.type === "attributes") applyAttrs(record.target);
                }
            } finally {
                busy = false;
            }
        });
        observer.observe(document.documentElement, {
            childList: true, subtree: true, characterData: true,
            attributes: true, attributeFilter: ATTRS,
        });
    }

    function refreshAll() {
        busy = true;
        try { walk(document.body); } finally { busy = false; }
        document.documentElement.lang = lang;
        document.title = lang === "en" ? "Imperial-CodeCenter" : "Imperial-CodeCenter";
        const toggle = document.getElementById("lang-toggle");
        if (toggle) {
            toggle.querySelectorAll("[data-lang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
        }
    }

    function set(next) {
        if (next !== "fr" && next !== "en") return;
        lang = next;
        try { window.localStorage.setItem(KEY, next); } catch { /* stockage indisponible : choix non mémorisé */ }
        refreshAll();
    }

    function mountToggle() {
        const foot = document.getElementById("site-foot");
        if (!foot || document.getElementById("lang-toggle")) return;
        const box = document.createElement("span");
        box.id = "lang-toggle";
        box.className = "lang-toggle";
        box.setAttribute("data-no-i18n", "");
        [["fr", "FR"], ["en", "EN"]].forEach(([code, label]) => {
            const b = document.createElement("button");
            b.type = "button";
            b.dataset.lang = code;
            b.textContent = label;
            b.setAttribute("aria-label", code === "fr" ? "Français" : "English");
            b.addEventListener("click", () => set(code));
            box.append(b);
        });
        foot.append(" · ", box);
    }

    ICC.i18n = { lang: () => lang, set, t };

    function start() {
        mountToggle();
        observe();
        refreshAll();
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
