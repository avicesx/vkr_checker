# Минимальный образ LibreOffice Writer для конвертации .doc/.rtf/.odt в .docx.
# Версию LO полезно фиксировать в метке:
#   docker build --build-arg LO_VERSION="$(docker run --rm docx-lo:1.0.0 soffice --version)" \
#       -t docx-lo:1.0.0 backend/docker/libreoffice
# Точная версия также пишется в /etc/docx-lo-version во время сборки.
FROM debian:trixie-slim

ARG LO_VERSION="unknown"
LABEL lo.version="${LO_VERSION}" \
      lo.version_file="/etc/docx-lo-version"

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=ru_RU.UTF-8 \
    LC_ALL=ru_RU.UTF-8 \
    HOME=/tmp

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui \
        fonts-liberation \
        fonts-dejavu-core \
        fonts-crosextra-carlito \
        fonts-crosextra-caladea \
        locales \
    && sed -i 's/^# *ru_RU.UTF-8/ru_RU.UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# Версия нужна для аудита: экспорт .docx зависит от версии LibreOffice.
RUN soffice --version > /etc/docx-lo-version 2>&1 \
    && test -s /etc/docx-lo-version

RUN useradd --create-home --uid 1000 converter
USER converter
WORKDIR /data

CMD ["soffice", "--headless", "--norestore", "--nologo"]
