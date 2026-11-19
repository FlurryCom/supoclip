FROM python:3.12-slim

WORKDIR /app

# TZ=UTC: ffmpeg names chunks by local time and the recorder parses them as UTC.
ENV PYTHONUNBUFFERED=1 \
    TZ=UTC

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && apt-get clean \
    && find /var/lib/apt/lists -type f -delete

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY supoclip_live/ ./supoclip_live/
COPY assets/ ./assets/

CMD ["python", "-m", "supoclip_live"]
