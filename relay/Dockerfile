# Ретранслятор вебхуков Конспекта: статический файл в пустом образе.
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /relay . && mkdir -p /data/events

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /relay /relay
# Образ без оболочки и не под root: каталог данных создаём при сборке и
# отдаём пользователю nonroot (65532), иначе он не сможет в него писать.
COPY --from=build --chown=65532:65532 /data /data
ENV RELAY_DATA=/data
VOLUME ["/data"]
EXPOSE 8080 80 443
ENTRYPOINT ["/relay"]
