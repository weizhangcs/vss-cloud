# Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

WORKDIR /app

COPY requirements.txt /app/

RUN mkdir -p /etc/apt/keyrings && \
    rm -f /etc/apt/sources.list && \
    rm -f /etc/apt/sources.list.d/* && \
    echo "deb http://mirrors.aliyun.com/debian/ trixie main non-free-firmware contrib" > /etc/apt/sources.list.d/aliyun.list && \
    echo "deb http://mirrors.aliyun.com/debian-security trixie-security main non-free-firmware contrib" >> /etc/apt/sources.list.d/aliyun.list && \
    echo "deb http://mirrors.aliyun.com/debian/ trixie-updates main non-free-firmware contrib" >> /etc/apt/sources.list.d/aliyun.list && \
    apt-get update && \
    apt-get install -y apt-transport-https ca-certificates gnupg debian-archive-keyring curl gettext && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

COPY . /app/