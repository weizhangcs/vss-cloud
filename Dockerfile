FROM ubuntu:latest
LABEL authors="wei_z"

ENTRYPOINT ["top", "-b"]