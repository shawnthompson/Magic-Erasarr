# Production image: runs gunicorn (WSGI), not the Flask dev server.
# Set DEBUG=true and STAT=true in env (e.g. docker-compose) as needed.
FROM python:3.14-alpine

ARG VERSION=1.8.0
LABEL maintainer="cbodden" \
      org.opencontainers.image.source="https://github.com/cbodden/Magic-Erasarr" \
      version="${VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py wsgi.py ./
COPY templates/ templates/
COPY utils/ utils/
COPY services/ services/
COPY routes/ routes/

EXPOSE 5000

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "wsgi:application"]
