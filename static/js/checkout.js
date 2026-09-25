/* Prices are display-only; checkout always derives payment terms on the server.
   Every plan on this page belongs to the same product, so selecting one only
   ever updates the order summary — never which course/offer is shown. */
(function () {
    const form = document.getElementById('checkout-form');
    if (!form) return;
    const radios = form.querySelectorAll('[name="plan_id"]');
    const button = document.getElementById('checkout-submit');
    const originalButton = button.innerHTML;
    const email = form.querySelector('[name="email"]');
    const phone = form.querySelector('[name="phone"]');
    const nameFields = ['first_name', 'last_name'].map(function (name) {
        const field = form.querySelector('[name="' + name + '"]');
        field.addEventListener('invalid', function () {
            field.setCustomValidity(name === 'first_name'
                ? 'Renseignez votre prénom.' : 'Renseignez votre nom.');
        });
        field.addEventListener('input', function () { field.setCustomValidity(''); });
        return field;
    });
    phone.addEventListener('invalid', function () {
        phone.setCustomValidity('Renseignez votre numéro de téléphone.');
    });
    phone.addEventListener('input', function () { phone.setCustomValidity(''); });
    email.addEventListener('invalid', function () {
        email.setCustomValidity(email.validity.valueMissing
            ? 'Renseignez votre adresse e-mail de réception.'
            : 'Saisissez une adresse e-mail valide.');
    });
    email.addEventListener('input', function () { email.setCustomValidity(''); });
    const fields = {
        'summary-name': 'name',
        'summary-price': 'total',
        'summary-total-due': 'due',
        'summary-installment-note': 'schedule',
        'summary-access': 'access',
    };
    function applySelection(radio) {
        if (!radio) return;
        Object.entries(fields).forEach(function ([id, key]) {
            document.getElementById(id).textContent = radio.dataset[key];
        });
        radios.forEach(function (option) { option.setCustomValidity(''); });
    }
    radios.forEach(function (radio) {
        radio.addEventListener('invalid', function () { radio.setCustomValidity('Choisissez votre plan d’accès.'); });
        radio.addEventListener('change', function () { applySelection(radio); });
    });
    // Do not silently select a different purchase after a server validation error.
    applySelection(form.querySelector('[name="plan_id"]:checked'));
    const firstError = form.querySelector('[aria-invalid="true"]');
    if (firstError) firstError.focus();
    form.addEventListener('submit', function (event) {
        if (button.disabled) { event.preventDefault(); return; }
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        button.textContent = 'Ouverture du paiement sécurisé…';
    });
    // Restore the button when returning from Tara via the browser back button.
    window.addEventListener('pageshow', function () {
        email.setCustomValidity('');
        phone.setCustomValidity('');
        nameFields.forEach(function (field) { field.setCustomValidity(''); });
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.innerHTML = originalButton;
        applySelection(form.querySelector('[name="plan_id"]:checked'));
    });
})();
