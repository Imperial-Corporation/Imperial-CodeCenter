// Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
// Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"use strict";

/*
 * Notifications (toasts) : `ICC.notify(message, type)` avec type "ok", "error", "info" ou "warn".
 * Le texte est inséré par textContent. La zone est annoncée aux lecteurs d'écran (aria-live).
 * Les toasts s'affichent au-dessus des boîtes de dialogue grâce au calque « top layer » (popover).
 */
(function () {
    const ICC = (window.ICC = window.ICC || {});
    const ICONS = { ok: "check", error: "alert", warn: "alert", info: "info" };
    const MAX_VISIBLE = 4;
    let region = null;

    function ensureRegion() {
        if (region && region.isConnected) return region;
        region = document.createElement("div");
        region.className = "toast-region";
        region.setAttribute("role", "status");
        region.setAttribute("aria-live", "polite");
        region.setAttribute("popover", "manual");
        document.body.append(region);
        return region;
    }

    function notify(message, type) {
        if (!message) return;
        const kind = ICONS[type] ? type : "info";
        const box = ensureRegion();
        const toast = document.createElement("div");
        toast.className = "toast";
        toast.dataset.kind = kind;
        if (ICC.icons) toast.append(ICC.icons.make(ICONS[kind], 16));
        const text = document.createElement("span");
        text.textContent = String(message);
        toast.append(text);
        while (box.children.length >= MAX_VISIBLE) box.firstChild.remove();
        box.append(toast);
        // Réaffiché à chaque toast : il repasse ainsi devant une boîte de dialogue ouverte après lui.
        try {
            if (box.matches(":popover-open")) box.hidePopover();
            box.showPopover();
        } catch {
            /* navigateur sans popover : le calque z-index suffit hors boîte de dialogue */
        }
        window.setTimeout(() => {
            toast.remove();
            if (!box.children.length) {
                try { box.hidePopover(); } catch { /* déjà masqué */ }
            }
        }, kind === "error" ? 8000 : 4000);
    }

    ICC.notify = notify;
})();
