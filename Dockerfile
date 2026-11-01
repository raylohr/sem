FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libgdal32 libgeos-c1v5 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 1000 --create-home sem
COPY --chown=sem:sem . .
RUN DJANGO_SECRET_KEY=build-only-not-used-at-runtime DB_PASSWORD=build-only python manage.py collectstatic --noinput
RUN mkdir -p /app/media && chown sem:sem /app/media && chown -R sem:sem /app/staticfiles
USER sem
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "--access-logformat", "%(h)s %(t)s %(m)s %(U)s %(s)s %(L)s", "--error-logfile", "-"]
