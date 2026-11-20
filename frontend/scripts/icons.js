// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Icônes de l'interface : petit sous-ensemble de la bibliothèque Lucide (licence ISC,
 * voir NOTICE), embarqué localement. Aucun CDN : la politique CSP du serveur n'autorise
 * que les scripts et styles de la même origine.
 *
 * `ICC.icons.make(nom, taille)` retourne un nœud <svg> (créé en DOM, sans innerHTML).
 * Un nom inconnu donne une icône vide plutôt qu'une erreur.
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});
    const NS = "http://www.w3.org/2000/svg";

    /* nom -> liste de [balise, attributs] (viewBox 24x24, trait 2) */
    const SHAPES = {
        search: [["circle", { cx: 11, cy: 11, r: 8 }], ["path", { d: "m21 21-4.3-4.3" }]],
        download: [["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }], ["path", { d: "m7 10 5 5 5-5" }], ["path", { d: "M12 15V3" }]],
        trash: [["path", { d: "M3 6h18" }], ["path", { d: "M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" }],
            ["path", { d: "M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" }], ["path", { d: "M10 11v6" }], ["path", { d: "M14 11v6" }]],
        copy: [["rect", { width: 14, height: 14, x: 8, y: 8, rx: 2, ry: 2 }],
            ["path", { d: "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" }]],
        save: [["path", { d: "M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" }],
            ["path", { d: "M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7" }], ["path", { d: "M7 3v4a1 1 0 0 0 1 1h7" }]],
        history: [["path", { d: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" }], ["path", { d: "M3 3v5h5" }], ["path", { d: "M12 7v5l4 2" }]],
        restore: [["path", { d: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" }], ["path", { d: "M3 3v5h5" }]],
        x: [["path", { d: "M18 6 6 18" }], ["path", { d: "m6 6 12 12" }]],
        refresh: [["path", { d: "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" }], ["path", { d: "M21 3v5h-5" }],
            ["path", { d: "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" }], ["path", { d: "M8 16H3v5" }]],
        check: [["path", { d: "M20 6 9 17l-5-5" }]],
        alert: [["path", { d: "m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3" }],
            ["path", { d: "M12 9v4" }], ["path", { d: "M12 17h.01" }]],
        info: [["circle", { cx: 12, cy: 12, r: 10 }], ["path", { d: "M12 16v-4" }], ["path", { d: "M12 8h.01" }]],
        users: [["path", { d: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" }], ["circle", { cx: 9, cy: 7, r: 4 }],
            ["path", { d: "M22 21v-2a4 4 0 0 0-3-3.87" }], ["path", { d: "M16 3.13a4 4 0 0 1 0 7.75" }]],
        library: [["path", { d: "M12 7v14" }],
            ["path", { d: "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" }]],
        database: [["ellipse", { cx: 12, cy: 5, rx: 9, ry: 3 }], ["path", { d: "M3 5v14a9 3 0 0 0 18 0V5" }], ["path", { d: "M3 12a9 3 0 0 0 18 0" }]],
        server: [["rect", { width: 20, height: 8, x: 2, y: 2, rx: 2, ry: 2 }], ["rect", { width: 20, height: 8, x: 2, y: 14, rx: 2, ry: 2 }],
            ["path", { d: "M6 6h.01" }], ["path", { d: "M6 18h.01" }]],
        archive: [["rect", { width: 20, height: 5, x: 2, y: 3, rx: 1 }], ["path", { d: "M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" }], ["path", { d: "M10 12h4" }]],
        key: [["path", { d: "m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4" }], ["path", { d: "m21 2-9.6 9.6" }],
            ["circle", { cx: 7.5, cy: 15.5, r: 5.5 }]],
        shield: [["path", { d: "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" }],
            ["path", { d: "m9 12 2 2 4-4" }]],
        pencil: [["path", { d: "M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" }],
            ["path", { d: "m15 5 4 4" }]],
        tag: [["path", { d: "M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z" }],
            ["circle", { cx: 7.5, cy: 7.5, r: 0.5 }]],
        sparkles: [["path", { d: "M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" }],
            ["path", { d: "M20 3v4" }], ["path", { d: "M22 5h-4" }]],
        left: [["path", { d: "m15 18-6-6 6-6" }]],
        right: [["path", { d: "m9 18 6-6-6-6" }]],
        up: [["path", { d: "m18 15-6-6-6 6" }]],
        down: [["path", { d: "m6 9 6 6 6-6" }]],
        file: [["path", { d: "M10 12.5 8 15l2 2.5" }], ["path", { d: "m14 12.5 2 2.5-2 2.5" }],
            ["path", { d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" }], ["path", { d: "M14 2v4a2 2 0 0 0 2 2h4" }]],
        play: [["path", { d: "M6 3 20 12 6 21z" }]],
        stop: [["rect", { width: 18, height: 18, x: 3, y: 3, rx: 2 }]],
        globe: [["circle", { cx: 12, cy: 12, r: 10 }], ["path", { d: "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" }], ["path", { d: "M2 12h20" }]],
        loader: [["path", { d: "M21 12a9 9 0 1 1-6.219-8.56" }]],
        filter: [["path", { d: "M22 3H2l8 9.46V19l4 2v-8.54z" }]],
        user: [["circle", { cx: 12, cy: 8, r: 4 }], ["path", { d: "M4 21c0-4 3.6-7 8-7s8 3 8 7" }]],
        lock: [["rect", { width: 18, height: 11, x: 3, y: 11, rx: 2, ry: 2 }], ["path", { d: "M7 11V7a5 5 0 0 1 10 0v4" }]],
    };

    function make(name, size) {
        const px = String(size || 16);
        const svg = document.createElementNS(NS, "svg");
        svg.setAttribute("width", px);
        svg.setAttribute("height", px);
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("fill", "none");
        svg.setAttribute("stroke", "currentColor");
        svg.setAttribute("stroke-width", "2");
        svg.setAttribute("stroke-linecap", "round");
        svg.setAttribute("stroke-linejoin", "round");
        svg.setAttribute("aria-hidden", "true");
        svg.classList.add("icon", `icon-${name}`);
        for (const [tag, attributes] of SHAPES[name] || []) {
            const shape = document.createElementNS(NS, tag);
            for (const [key, value] of Object.entries(attributes)) shape.setAttribute(key, String(value));
            svg.append(shape);
        }
        return svg;
    }

    ICC.icons = { make, names: () => Object.keys(SHAPES) };
})();
