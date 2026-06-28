FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/usr/lib/android-sdk/platform-tools:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends adb android-sdk-platform-tools ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-bot.txt /app/requirements-bot.txt
RUN pip install --no-cache-dir -r /app/requirements-bot.txt

COPY . /app

CMD ["python", "telegram_bot.py"]
