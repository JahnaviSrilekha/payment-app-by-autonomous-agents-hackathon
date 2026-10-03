# Pocketful — stage 4: build and run

The service is a single Python 3.12 process using only the standard library — there are
no dependencies to install and no runtime network access is needed.

## Build

```sh
docker build -t pocketful-stage4 .
```

(run from this `stage-4/` directory, which contains the `Dockerfile`)

## Start

```sh
docker run --rm -e PORT=8080 -p 8080:8080 pocketful-stage4
```

The service listens on `0.0.0.0` on the port given by the `PORT` environment variable
(default `8080`) and answers `GET /health` with `200 {"status": "ok"}` as soon as it is
ready, well within 60 seconds of container start.

## One-liner (build and start without manual setup)

```sh
docker build -t pocketful-stage4 . && docker run --rm -e PORT=8080 -p 8080:8080 pocketful-stage4
```

## Running without Docker

```sh
PORT=8080 python3 src/server.py
```

(works with any Python 3.12+ interpreter; no packages required)

## Test control endpoints

`POST /_test/reset`, `GET /_test/export` and `POST /_test/import` are enabled in the
delivered image and require no authentication.