/* slideover.js - generic right-side detail panel, ported from the "Le Comptoir"
   dashboard design. Any element with data-panel-url opens it: fetches that
   URL (expected to return JSON: {title, subtitle, actions_html, body_html}),
   injects the pieces, and shows the panel. No dependency on Bootstrap's JS.

   Reused across screens (Cours today; Contacts/other list pages follow the
   same [data-panel-url] contract) rather than each page wiring its own panel. */
document.addEventListener('DOMContentLoaded', function () {
    var scrim = document.getElementById('global-scrim');
    var panel = document.getElementById('global-slideover');
    var titleEl = document.getElementById('so-title');
    var subEl = document.getElementById('so-sub');
    var actionsEl = document.getElementById('so-actions');
    var bodyEl = document.getElementById('so-body');
    var closeBtn = document.getElementById('so-close');
    var avatarEl = document.getElementById('so-avatar');
    if (!scrim || !panel) return;

    var lastFocused = null;

    function openPanel() {
        lastFocused = document.activeElement;
        scrim.classList.add('open');
        panel.classList.add('open');
        panel.setAttribute('aria-hidden', 'false');
        document.addEventListener('keydown', onKeydown);
    }

    function closePanel() {
        scrim.classList.remove('open');
        panel.classList.remove('open');
        panel.setAttribute('aria-hidden', 'true');
        document.removeEventListener('keydown', onKeydown);
        if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
    }

    function onKeydown(e) {
        if (e.key === 'Escape') closePanel();
    }

    scrim.addEventListener('click', closePanel);
    if (closeBtn) closeBtn.addEventListener('click', closePanel);

    document.addEventListener('click', function (e) {
        var trigger = e.target.closest('[data-panel-url]');
        if (!trigger) return;
        e.preventDefault();

        titleEl.textContent = trigger.getAttribute('data-panel-title') || '';
        subEl.textContent = '';
        actionsEl.innerHTML = '';
        bodyEl.innerHTML = '<div class="so-loading">Chargement…</div>';
        if (avatarEl) avatarEl.hidden = true;
        openPanel();

        fetch(trigger.getAttribute('data-panel-url'), { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (response) {
                if (!response.ok) throw new Error('bad response');
                return response.json();
            })
            .then(function (data) {
                titleEl.textContent = data.title || '';
                subEl.textContent = data.subtitle || '';
                actionsEl.innerHTML = data.actions_html || '';
                bodyEl.innerHTML = data.body_html || '';
                if (avatarEl) {
                    if (data.initials) {
                        avatarEl.textContent = data.initials;
                        avatarEl.hidden = false;
                    } else {
                        avatarEl.hidden = true;
                    }
                }
            })
            .catch(function () {
                bodyEl.innerHTML = '<div class="so-loading">Impossible de charger ce contenu.</div>';
            });
    });

    // Toggle-all checkboxes inside a fetched panel body (e.g. the course
    // picker) — data-select-target is a CSS selector for the checkboxes.
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.js-select-all');
        if (!btn) return;
        var selector = btn.getAttribute('data-select-target');
        if (!selector) return;
        var boxes = document.querySelectorAll(selector);
        var allChecked = boxes.length > 0 && Array.prototype.every.call(boxes, function (b) { return b.checked; });
        boxes.forEach(function (b) { b.checked = !allChecked; });
        btn.textContent = allChecked ? 'Tout sélectionner' : 'Tout désélectionner';
    });

    // Copy-to-clipboard for any button inside a fetched panel body
    // (data-copy-target points at the id of the element holding the text).
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.js-copy');
        if (!btn) return;
        var targetId = btn.getAttribute('data-copy-target');
        var field = targetId ? document.getElementById(targetId) : btn.previousElementSibling;
        if (!field) return;
        var text = (field.value !== undefined ? field.value : field.textContent).trim();
        if (!text) return;
        try { navigator.clipboard.writeText(text); } catch (err) { /* clipboard unavailable — no-op */ }
        var original = btn.textContent;
        btn.textContent = 'Copié !';
        setTimeout(function () { btn.textContent = original; }, 1500);
    });
});
