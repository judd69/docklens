# Clean Dockerfile — the shape DockLens wants to see.
# Run: python -m docklens examples/good/Dockerfile
FROM node:20.11.0-alpine3.19
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY . .
RUN addgroup -S app && adduser -S app -G app
USER app
EXPOSE 3000
HEALTHCHECK CMD node -e "fetch('http://localhost:3000/health').then(r => { if (!r.ok) process.exit(1) })" || exit 1
CMD ["node", "server.js"]
