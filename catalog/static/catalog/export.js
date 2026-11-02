(() => {
    const link = document.getElementById('exportSourceData');
    const status = document.getElementById('exportStatus');
    if (!link || !status) return;
    let busy = false;

    link.addEventListener('click', async (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (busy) return;
        busy = true;
        link.classList.add('disabled');
        link.setAttribute('aria-disabled', 'true');
        link.setAttribute('aria-busy', 'true');
        link.querySelector('[data-export-spinner]').hidden = false;
        link.querySelector('[data-export-label]').textContent = 'Export wordt voorbereid…';
        status.classList.remove('text-danger');
        status.textContent = 'De export loopt. Laat deze pagina open; de download start zodra het bestand klaar is.';
        let objectURL;
        try {
            const response = await fetch(link.href, {credentials: 'same-origin'});
            if (!response.ok || !response.headers.get('Content-Type')?.startsWith('application/zip')) {
                throw new Error('Export failed');
            }
            const blob = await response.blob();
            objectURL = URL.createObjectURL(blob);
            const download = document.createElement('a');
            download.href = objectURL;
            const filename = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/);
            download.download = filename ? filename[1] : 'seo-brondata.zip';
            document.body.appendChild(download);
            download.click();
            download.remove();
            status.textContent = 'Export klaar. De download is gestart.';
        } catch (error) {
            status.classList.add('text-danger');
            status.textContent = 'Exporteren is niet gelukt. Probeer opnieuw. Vernieuw de pagina als je sessie is verlopen.';
        } finally {
            if (objectURL) setTimeout(() => URL.revokeObjectURL(objectURL), 60000);
            busy = false;
            link.classList.remove('disabled');
            link.removeAttribute('aria-disabled');
            link.removeAttribute('aria-busy');
            link.querySelector('[data-export-spinner]').hidden = true;
            link.querySelector('[data-export-label]').textContent = 'Exporteer brondata';
        }
    });
})();
