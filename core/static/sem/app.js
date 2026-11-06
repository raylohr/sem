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

// Open feedback before making the request; report real server work phases.
document.querySelectorAll('[data-excel-export]').forEach((button) => {
  const modalElement = document.getElementById('excelExportModal');
  if (!modalElement || !window.bootstrap) return;
  const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
  const message = document.getElementById('excelExportMessage');
  const progress = document.getElementById('excelExportProgress');
  const bar = progress.querySelector('.progress-bar');
  const elapsed = document.getElementById('excelExportElapsed');
  const download = document.getElementById('excelExportDownload');
  let running = false;
  let objectUrl = null;
  let generation = 0;
  button.addEventListener('click', async (event) => {
    event.preventDefault();
    if (running) { modal.show(); return; }
    running = true;
    const currentGeneration = ++generation;
    button.setAttribute('aria-disabled', 'true');
    button.setAttribute('aria-busy', 'true');
    download.classList.add('d-none');
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    message.className = '';
    message.textContent = 'Export wordt gestart…';
    progress.classList.remove('d-none');
    progress.removeAttribute('aria-valuenow');
    bar.className = 'progress-bar progress-bar-striped progress-bar-animated';
    bar.style.width = '100%';
    elapsed.textContent = 'Verstreken tijd: 0 seconden';
    modal.show();
    const started = Date.now();
    const token = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : null;
    const exportUrl = new URL(button.href, window.location.href);
    const statusUrl = new URL(button.dataset.progressUrl, window.location.href);
    if (token) { exportUrl.searchParams.set('export_token', token); statusUrl.searchParams.set('export_token', token); }
    let pollTimer;
    const elapsedTimer = setInterval(() => {
      elapsed.textContent = `Verstreken tijd: ${Math.floor((Date.now() - started) / 1000)} seconden`;
    }, 1000);
    const poll = async () => {
      try {
        const response = await fetch(statusUrl, {credentials: 'same-origin', cache: 'no-store', redirect: 'error'});
        if (response.ok && (response.headers.get('content-type') || '').includes('application/json')) {
          const status = await response.json();
          if (running && generation === currentGeneration && status.state !== 'waiting' && status.message) {
            message.textContent = status.message;
            const percent = Math.max(0, Math.min(100, Number(status.percent) || 0));
            progress.setAttribute('aria-valuenow', String(percent));
            bar.style.width = `${percent}%`;
          }
        }
      } catch (_) { /* The download remains active if progress feedback is unavailable. */ }
      if (running && generation === currentGeneration) pollTimer = setTimeout(poll, 1000);
    };
    if (token) pollTimer = setTimeout(poll, 500);
    try {
      const response = await fetch(exportUrl, {credentials: 'same-origin', cache: 'no-store', redirect: 'error'});
      const type = response.headers.get('content-type') || '';
      if (!response.ok) {
        const detail = type.includes('text/plain') ? (await response.text()).slice(0, 500) : '';
        throw new Error(detail || 'Exporteren is mislukt. Probeer opnieuw of vernieuw de pagina.');
      }
      if (!type.includes('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')) {
        throw new Error('Je sessie is mogelijk verlopen. Vernieuw de pagina en log zo nodig opnieuw in.');
      }
      message.textContent = 'Excelbestand ontvangen…';
      const blob = await response.blob();
      objectUrl = URL.createObjectURL(blob);
      let filename = button.dataset.exportFilename || 'url-werkbestand.xlsx';
      const disposition = response.headers.get('content-disposition') || '';
      const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const plain = disposition.match(/filename="([^"]+)"/i);
      if (encoded) { try { filename = decodeURIComponent(encoded[1]); } catch (_) {} }
      else if (plain) filename = plain[1];
      download.href = objectUrl;
      download.download = filename;
      download.classList.remove('d-none');
      download.click();
      message.textContent = 'Het Excelbestand is gereed. De download is gestart. Gebruik de knop hieronder als de download niet verschijnt.';
      progress.setAttribute('aria-valuenow', '100');
      bar.style.width = '100%';
      bar.className = 'progress-bar bg-success';
    } catch (error) {
      message.className = 'text-danger';
      message.textContent = error.message || 'Er is een verbindingsfout opgetreden. Probeer opnieuw.';
      progress.classList.add('d-none');
    } finally {
      running = false;
      clearInterval(elapsedTimer);
      clearTimeout(pollTimer);
      button.removeAttribute('aria-disabled');
      button.removeAttribute('aria-busy');
    }
  });
});
