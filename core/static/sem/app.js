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
  if (!message && format && input.type !== 'number') input.value = integerFormatter.format(value);
};
document.querySelectorAll('input[data-value-type="integer"]:not([type="hidden"])').forEach(input => {
  validateInteger(input, true);
  input.addEventListener('input', () => validateInteger(input));
  input.addEventListener('blur', () => validateInteger(input, true));
});
document.querySelectorAll('form[data-scan-config]').forEach(editor => {
  const scanForm = document.getElementById(editor.dataset.scanConfig);
  const modal = editor.closest('.modal');
  const card = scanForm.closest('[data-scan-card]');
  const names = ['url', 'max_urls', 'max_depth', 'robots_mode', 'concurrency', 'requests_per_second', 'connect_timeout', 'read_timeout', 'max_attempts', 'title_min_chars', 'title_max_chars', 'description_min_chars', 'description_max_chars', 'heading_max_chars', 'check_resources', 'check_external_links', 'resource_max_targets', 'external_max_targets', 'resource_max_bytes', 'external_max_bytes', 'resource_file_bytes', 'resource_max_depth', 'image_advice_bytes', 'browser_phase','browser_max_pages','browser_timeout_seconds','schema_profiles'].filter(name => editor.elements.namedItem(name) && scanForm.elements.namedItem(name));
  const restore = () => {
    names.forEach(name => {
      const input = editor.elements.namedItem(name);
      if(input.multiple){const selected=scanForm.elements.namedItem(name).value.split(',');Array.from(input.options).forEach(option=>option.selected=selected.includes(option.value));}else input.value = scanForm.elements.namedItem(name).value;
      if (input.dataset.valueType === 'integer') validateInteger(input, true);
    });
  };
  modal.addEventListener('show.bs.modal', restore);
  modal.addEventListener('hidden.bs.modal', restore);
  modal.addEventListener('shown.bs.modal', () => editor.elements.namedItem('url').focus());
  editor.addEventListener('submit', async event => {
    event.preventDefault();
    if(editor.dataset.saving==='true') return;
    editor.querySelectorAll('input[data-value-type="integer"]').forEach(input => validateInteger(input));
    if (!editor.reportValidity()) return;
    const values={kind:scanForm.elements.namedItem('kind').value};
    names.forEach(name=>{
      const input=editor.elements.namedItem(name);
      values[name]=input.multiple ? Array.from(input.selectedOptions).map(option=>option.value) : input.dataset.valueType==='integer' ? parseInteger(input.value) : input.value==='true' ? true : input.value==='false' ? false : input.value.trim();
    });
    const button=editor.querySelector('button[type="submit"]');
    const errorMessage=editor.querySelector('[data-scan-settings-result]');
    errorMessage.textContent='';editor.dataset.saving='true';button.disabled=true;
    try {
      const response=await fetch(editor.dataset.saveUrl,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json','X-CSRFToken':scanForm.elements.namedItem('csrfmiddlewaretoken').value},body:JSON.stringify(values)});
      if(response.redirected) throw new Error('Je sessie is verlopen. Log opnieuw in.');
      if(!response.headers.get('content-type')?.includes('application/json')) throw new Error('Instellingen konden niet worden opgeslagen. Vernieuw de pagina en probeer opnieuw.');
      const payload=await response.json();
      if(!response.ok){
        const errors=payload.errors ? Object.entries(payload.errors).flatMap(([field,items])=>items.map(item=>`${field}: ${item.message}`)).join(' · ') : '';
        throw new Error(payload.error || errors || 'Instellingen konden niet worden opgeslagen.');
      }
      Object.entries(payload.settings).forEach(([name,value])=>{
        const input=scanForm.elements.namedItem(name);if(!input) return;
        input.value=Array.isArray(value)?value.join(','):String(value);
        const summary=card.querySelector(`[data-scan-summary="${name}"]`);
        if(summary) summary.textContent=name==='url'?(value||card.dataset.defaultUrl):integerFormatter.format(value);
      });
      card.querySelector('[data-scan-config-message]').textContent='Instellingen opgeslagen voor deze website. Volgende onderzoeken gebruiken deze waarden.';
      const result=scanForm.querySelector('.form-result');result.textContent='';result.classList.add('d-none');
      window.bootstrap.Modal.getOrCreateInstance(modal).hide();
    } catch(error) {errorMessage.textContent=error.message || 'Opslaan mislukt. Probeer opnieuw.';}
    finally {editor.dataset.saving='false';button.disabled=false;}
  });
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
