FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl && rm -rf /var/lib/apt/lists/*
RUN curl -sL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && chmod a+rx /usr/local/bin/yt-dlp
RUN pip install --no-cache-dir flask requests

WORKDIR /app
COPY app.py .
COPY templates ./templates

EXPOSE 5000
CMD ["python", "app.py"]
