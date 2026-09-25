/* tabs.js - generic page-level tab switcher, ported from the "Le Comptoir"
   design's .tabs/.tab pattern. A `[data-tabs]` container holds `.tab`
   triggers (`data-tab-target="#pane-id"`); panes are plain elements with a
   matching id, hidden via the `hidden` attribute. No dependency on
   Bootstrap's own tab JS — .tab is a distinct, deliberately simpler
   component, not Bootstrap's .nav-link. */
document.addEventListener('DOMContentLoaded', function () {
    function activate(group, tab) {
        var targetId = tab.getAttribute('data-tab-target');
        var target = targetId ? document.querySelector(targetId) : null;
        group.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        group.querySelectorAll('[data-tab-pane]').forEach(function (pane) { pane.hidden = true; });
        if (target) target.hidden = false;
    }

    document.querySelectorAll('[data-tabs]').forEach(function (group) {
        var tabs = group.querySelectorAll('.tab');
        tabs.forEach(function (tab) {
            tab.addEventListener('click', function () { activate(group, tab); });
        });

        // A redirect back here can carry #tab-xyz (e.g. after enrolling a
        // contact into a course) — land on that tab instead of always Aperçu.
        if (window.location.hash) {
            var matching = group.querySelector('.tab[data-tab-target="' + window.location.hash + '"]');
            if (matching) activate(group, matching);
        }
    });
});
