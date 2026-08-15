/* action-menu.js - small, dependency-free dropdown menu for row actions.
   Does not rely on Bootstrap's JS bundle (loaded from a CDN that can fail
   or be blocked) and positions the menu with position:fixed, computed
   here, so it is never clipped by an ancestor with overflow:hidden/auto
   (e.g. .table-container / .table-responsive). */
document.addEventListener('DOMContentLoaded', function () {
    var OPEN_CLASS = 'is-open';
    var openMenu = null;
    var openToggle = null;
    var openedAt = 0;

    function closeOpenMenu() {
        if (!openMenu) return;
        openMenu.classList.remove(OPEN_CLASS);
        if (openToggle) openToggle.setAttribute('aria-expanded', 'false');
        openMenu = null;
        openToggle = null;
    }

    function positionMenu(toggle, menu) {
        var rect = toggle.getBoundingClientRect();

        // Render off-screen first so its natural size can be measured.
        menu.style.left = '-9999px';
        menu.style.top = '0px';
        menu.classList.add(OPEN_CLASS);
        var menuWidth = menu.offsetWidth;
        var menuHeight = menu.offsetHeight;
        menu.classList.remove(OPEN_CLASS);

        var viewportWidth = document.documentElement.clientWidth;
        var viewportHeight = document.documentElement.clientHeight;

        // Right-align to the toggle by default; keep it inside the viewport.
        var left = rect.right - menuWidth;
        if (left < 8) left = Math.max(8, rect.left);
        if (left + menuWidth > viewportWidth - 8) left = viewportWidth - menuWidth - 8;

        // Open below the toggle; flip above it if that would overflow the bottom.
        var top = rect.bottom + 4;
        if (top + menuHeight > viewportHeight - 8) top = rect.top - menuHeight - 4;

        // Clamp: if the toggle itself is near/outside a viewport edge (e.g. a
        // row scrolled only partially into view), the flip above can still
        // land off-screen — pin the menu fully inside the viewport as a
        // last resort so it's never clipped or unreachable.
        left = Math.min(Math.max(left, 8), viewportWidth - menuWidth - 8);
        top = Math.min(Math.max(top, 8), viewportHeight - menuHeight - 8);

        menu.style.left = left + 'px';
        menu.style.top = top + 'px';
    }

    function openMenuFor(toggle, menu) {
        if (openMenu === menu) {
            closeOpenMenu();
            return;
        }
        closeOpenMenu();
        positionMenu(toggle, menu);
        menu.classList.add(OPEN_CLASS);
        toggle.setAttribute('aria-expanded', 'true');
        openMenu = menu;
        openToggle = toggle;
        openedAt = Date.now();
        var firstItem = menu.querySelector('.action-menu-item');
        if (firstItem) firstItem.focus();
    }

    document.addEventListener('click', function (event) {
        var toggle = event.target.closest('.action-menu-toggle');
        if (toggle) {
            event.preventDefault();
            event.stopPropagation();
            var menu = toggle.parentElement.querySelector('.action-menu-list');
            if (menu) openMenuFor(toggle, menu);
            return;
        }
        if (openMenu && !openMenu.contains(event.target)) {
            closeOpenMenu();
        }
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && openMenu) {
            var toggle = openToggle;
            closeOpenMenu();
            if (toggle) toggle.focus();
            return;
        }
        if (!openMenu) return;
        var items = Array.prototype.slice.call(openMenu.querySelectorAll('.action-menu-item'));
        var currentIndex = items.indexOf(document.activeElement);
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            var next = items[currentIndex + 1] || items[0];
            if (next) next.focus();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            var prev = items[currentIndex - 1] || items[items.length - 1];
            if (prev) prev.focus();
        }
    });

    function closeOnScroll() {
        // Opening a menu near a viewport edge — or focusing its first item —
        // can trigger the browser's own "scroll the focused element into
        // view" behavior a moment later. Without this guard, that scroll
        // event immediately closes the menu we just opened. Only treat a
        // scroll as "user scrolled away" once the menu has been open a beat.
        if (Date.now() - openedAt < 150) return;
        closeOpenMenu();
    }

    window.addEventListener('resize', closeOpenMenu);
    window.addEventListener('scroll', closeOnScroll, true);
});
