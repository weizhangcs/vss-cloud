# Dockerfile
# FROM python:3.12-slim 官方Python精简镜像
# 国内阿里云的镜像仓库
FROM crpi-34v4qt829vtet2cy.cn-hangzhou.personal.cr.aliyuncs.com/vss_base/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/

RUN mkdir -p /etc/apt/keyrings && \
    rm -f /etc/apt/sources.list && \
    rm -f /etc/apt/sources.list.d/* && \
    echo "deb http://mirrors.aliyun.com/debian/ trixie main non-free-firmware contrib" > /etc/apt/sources.list.d/aliyun.list && \
    echo "deb http://mirrors.aliyun.com/debian-security trixie-security main non-free-firmware contrib" >> /etc/apt/sources.list.d/aliyun.list && \
    echo "deb http://mirrors.aliyun.com/debian/ trixie-updates main non-free-firmware contrib" >> /etc/apt/sources.list.d/aliyun.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends apt-transport-https ca-certificates gnupg debian-archive-keyring curl gettext ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

COPY . /app/