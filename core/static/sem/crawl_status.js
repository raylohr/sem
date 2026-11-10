(() => {
  const panel = document.querySelector('[data-crawl-status-url]');
  if (!panel) return;
  let pending = false;
  setInterval(async () => {
    if (pending || document.hidden || panel.contains(document.activeElement)) return;
    pending = true;
    try {
      const response = await fetch(panel.dataset.crawlStatusUrl, {
        credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      });
      if (response.ok) panel.innerHTML = await response.text();
    } catch (_) {
      // Keep the last known status. Saving settings never depends on polling.
    } finally {
      pending = false;
    }
  }, 10000);
})();
