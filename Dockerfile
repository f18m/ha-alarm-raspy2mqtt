# Stage 1: the builder
FROM python:3.12-slim AS builder

WORKDIR /app
RUN mkdir /app/raspy2mqtt
ADD raspy2mqtt /app/raspy2mqtt
COPY pyproject.toml README.md /app
RUN ls -l /app
RUN pip3 install build

# produce the wheel
RUN python3 -m build


# Stage 2: Create the final image
FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/f18m/ha-alarm-raspy2mqtt

# install the wheel
WORKDIR /app
COPY --from=builder /app/dist/*.whl .
RUN pip3 install --no-cache-dir *.whl

ENV PYTHONUNBUFFERED=1
CMD [ "raspy2mqtt" ]
