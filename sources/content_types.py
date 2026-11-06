"""Presentation of a crawler's own response, without substituting another source."""

def content_badge(data, source='sf'):
    mime = str(data.get('Content Type' if source == 'sf' else 'Content type', '')).split(';')[0].strip().lower()
    raw_status = data.get('Status Code' if source == 'sf' else 'HTTP status code', '')
    try:
        status = int(float(raw_status))
    except (ValueError, TypeError, OverflowError):
        status = None
    if status == 0 or (status is not None and status >= 400):
        label, tone = ('Geen response' if status == 0 else f'HTTP {status}'), 'error'
    elif status is not None and 300 <= status < 400:
        label, tone = f'Omleiding {status}', 'other'
    elif mime in ('text/html', 'application/xhtml+xml'):
        label, tone = 'HTML', 'html'
    elif mime.startswith('image/'):
        label, tone = 'Afbeelding', 'other'
    elif mime in ('application/javascript', 'text/javascript', 'application/x-javascript'):
        label, tone = 'JavaScript', 'other'
    elif mime == 'text/css':
        label, tone = 'CSS', 'other'
    elif mime == 'application/pdf':
        label, tone = 'PDF', 'other'
    elif 'font' in mime or mime == 'application/vnd.ms-fontobject':
        label, tone = 'Lettertype', 'other'
    elif 'xml' in mime:
        label, tone = 'XML/feed', 'other'
    elif mime.startswith('video/'):
        label, tone = 'Video', 'other'
    elif mime.startswith('audio/'):
        label, tone = 'Audio', 'other'
    elif mime == 'application/json':
        label, tone = 'JSON', 'other'
    elif mime.startswith('text/'):
        label, tone = 'Tekst', 'other'
    else:
        label, tone = ('Overig' if mime else 'Onbekend'), 'other'
    return {'label': label, 'tone': tone, 'mime': mime or 'Onbekend contenttype',
            'status': str(status) if status is not None else 'Onbekende HTTP-status'}
