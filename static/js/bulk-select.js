/* bulk-select.js - wires up row checkboxes + a bulk-actions bar for tables
   that opt into components/bulk_actions.html and a `.row-select` checkbox
   column (components/table_start.html with selectable=True). No-ops safely
   on pages that don't have these elements. */
document.addEventListener('DOMContentLoaded', function () {
    var bulkBar = document.getElementById('bulk-actions-bar');
    if (!bulkBar) return;

    var selects = document.querySelectorAll('.row-select');
    var countSpan = bulkBar.querySelector('.selected-count');
    var selectAll = document.querySelector('.select-all');
    var cancelBtn = bulkBar.querySelector('.cancel-selection');

    function updateBar() {
        var checked = document.querySelectorAll('.row-select:checked').length;
        if (countSpan) countSpan.textContent = checked;
        bulkBar.classList.toggle('show', checked > 0);
    }

    selects.forEach(function (checkbox) {
        checkbox.addEventListener('change', updateBar);
    });

    if (selectAll) {
        selectAll.addEventListener('change', function () {
            selects.forEach(function (checkbox) {
                checkbox.checked = selectAll.checked;
            });
            updateBar();
        });
    }

    if (cancelBtn) {
        cancelBtn.addEventListener('click', function () {
            selects.forEach(function (checkbox) {
                checkbox.checked = false;
            });
            if (selectAll) selectAll.checked = false;
            updateBar();
        });
    }
});
