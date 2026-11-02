'use strict';
const integerFormatter = new Intl.NumberFormat('nl-NL', {maximumFractionDigits: 0});
const parseInteger = (value) => {
  const text = String(value).trim();
  if (!/^[+-]?(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)$/.test(text)) return null;
  const number = Number(text.replaceAll('.', ''));
  return Number.isSafeInteger(number) ? number : null;
};
const validateInteger = (input, format = false) => {
  if (!input.value.trim()) { input.setCustomValidity(''); return; }
  const value = parseInteger(input.value);
  let message = '';
  if (value === null) message = 'Gebruik een geheel getal, bijvoorbeeld 100.000.';
  else if (input.dataset.min !== undefined && value < Number(input.dataset.min)) message = `Gebruik minimaal ${integerFormatter.format(Number(input.dataset.min))}.`;
  else if (input.dataset.max !== undefined && value > Number(input.dataset.max)) message = `Gebruik maximaal ${integerFormatter.format(Number(input.dataset.max))}.`;
  input.setCustomValidity(message);
  if (!message && format) input.value = integerFormatter.format(value);
};
document.querySelectorAll('input[data-value-type="integer"]').forEach(input => {
  validateInteger(input, true);
  input.addEventListener('input', () => validateInteger(input));
  input.addEventListener('blur', () => validateInteger(input, true));
});
document.querySelectorAll('form[data-api-form]').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    form.querySelectorAll('input[data-value-type="integer"]').forEach(input => validateInteger(input));
    if (!form.reportValidity()) return;
    const result = form.querySelector('.form-result');
    const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
    const submitter = event.submitter;
    const data = new FormData(form);
    if (submitter?.name) data.set(submitter.name, submitter.value);
    const preview = data.get('preview') === '1';
    const headers = {'X-CSRFToken': data.get('csrfmiddlewaretoken') || ''};
    data.delete('csrfmiddlewaretoken');
    let body = data;
    if (form.dataset.encoding === 'json') {
      const payload = {};
      for (const [key, value] of data.entries()) {
        const input = form.elements.namedItem(key);
        if (input?.dataset.valueType === 'list') payload[key] = String(value).split(',').map(s => s.trim()).filter(Boolean);
        else if (input?.dataset.valueType === 'integer') { if (String(value).trim() !== '') payload[key] = parseInteger(value); }
        else if (input?.type === 'number') { if (value !== '') payload[key] = Number(value); }
        else if (value !== '') payload[key] = value;
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
    show(preview ? 'Bestand controleren…' : 'Bezig…');
    try {
      const response = await fetch(form.action, {method: 'POST', body, headers, credentials: 'same-origin', redirect: 'error'});
      const type = response.headers.get('content-type') || '';
      if (!type.includes('application/json')) throw new Error('Je sessie is mogelijk verlopen. Vernieuw de pagina en log zo nodig opnieuw in.');
      const payload = await response.json();
      if (!response.ok) {
        const errors = payload.errors ? Object.entries(payload.errors).flatMap(([field, items]) => items.map(item => `${field}: ${item.message}`)).join('\n') : '';
        throw new Error(payload.error || errors || 'De actie is niet gelukt. Probeer opnieuw.');
      }
      if (preview) {
        const sheets = (payload.sheets || []).map(s => `${s.name}: ${integerFormatter.format(s.rows)} rijen`).join('\n');
        show(`Bestand herkend · ${integerFormatter.format(payload.record_count)} bronrecords\n${sheets}\n${(payload.warnings || []).join('\n')}\n\nKlik op Importeren om het bestand toe te voegen.`);
      } else if (form.dataset.oauth && payload.authorization_url) {
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
