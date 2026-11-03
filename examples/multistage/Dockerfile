# Multi-stage build with one classic slip: the final stage never
# copies the built artifact out of the builder stage.
# Run: python -m docklens examples/multistage/Dockerfile
FROM node:20.11.0-alpine3.19 AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20.11.0-alpine3.19
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
USER 10001
EXPOSE 3000
HEALTHCHECK CMD node healthcheck.js
CMD ["node", "server.js"]
