'use strict';
document.querySelectorAll('form[data-api-form]').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const result = form.querySelector('.form-result');
    const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
    const data = new FormData(form);
    const headers = {'X-CSRFToken': data.get('csrfmiddlewaretoken') || ''};
    data.delete('csrfmiddlewaretoken');
    let body = data;
    if (form.dataset.encoding === 'json') {
      const payload = {};
      for (const [key, value] of data.entries()) {
        if (value !== '') payload[key] = value;
      }
      body = JSON.stringify(payload);
      headers['Content-Type'] = 'application/json';
    }
    const show = (message, error = false) => {
      if (!result) return;
      result.className = `form-result mt-3 alert ${error ? 'alert-danger' : 'alert-info'}`;
      result.textContent = message;
    };
    buttons.forEach(button => button.disabled = true);
    form.setAttribute('aria-busy', 'true');
    show('Bezig…');
    try {
      const response = await fetch(form.action, {method: 'POST', body, headers, credentials: 'same-origin', redirect: 'error'});
      const type = response.headers.get('content-type') || '';
      if (!type.includes('application/json')) throw new Error('Je sessie is mogelijk verlopen. Vernieuw de pagina en log zo nodig opnieuw in.');
      const payload = await response.json();
      if (!response.ok) {
        const errors = payload.errors ? Object.entries(payload.errors).flatMap(([field, items]) => items.map(item => `${field}: ${item.message}`)).join('\n') : '';
        throw new Error(payload.error || errors || 'De actie is niet gelukt. Probeer opnieuw.');
      }
      if (form.dataset.oauth && payload.authorization_url) {
        const destination = new URL(payload.authorization_url);
        if (destination.origin !== 'https://accounts.google.com') throw new Error('Ongeldige Google-koppelpagina.');
        window.location.assign(destination.href);
      } else if (form.dataset.redirect) window.location.assign(form.dataset.redirect);
      else if (form.dataset.batchBase && payload.id) window.location.assign(`${form.dataset.batchBase}${encodeURIComponent(payload.id)}/`);
      else if (form.dataset.success === 'reload') window.location.reload();
      else show('Opgeslagen.');
    } catch (error) { show(error.message || 'Er is een verbindingsfout opgetreden.', true); }
    finally { buttons.forEach(button => button.disabled = false); form.removeAttribute('aria-busy'); }
  });
});
