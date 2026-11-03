# Deliberately bad Dockerfile — every line is a lesson.
# Run: python -m docklens examples/bad/Dockerfile
FROM node:latest
MAINTAINER someone@example.com
ENV API_KEY=placeholder-value
ARG NPM_TOKEN
RUN curl -sSL https://example.com/install.sh | sh
RUN apt-get update
RUN apt-get install -y ffmpeg git
ADD https://example.com/tool.tar.gz /opt/
COPY . .
RUN NPM_TOKEN=$NPM_TOKEN npm install
EXPOSE 22 3000
CMD npm start
