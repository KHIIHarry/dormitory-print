FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps
COPY index.html tsconfig.json vite.config.ts ./
COPY scripts/generate-pwa.mjs ./scripts/generate-pwa.mjs
COPY src ./src
COPY public ./public
ENV VITE_API_BASE_URL=/api/v1 \
    VITE_ADMIN_BASE_PATH=/manage \
    VITE_DEMO_MODE=false
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY --chmod=0644 deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --chmod=0644 deploy/nginx/templates/default.conf.template /etc/nginx/templates/default.conf.template
COPY --chmod=0755 deploy/nginx/19-local-config.sh /docker-entrypoint.d/19-local-config.sh
USER root
RUN chmod 0755 /etc/nginx/templates \
    && sed -i 's|include /etc/nginx/conf.d/\*.conf;|include /tmp/conf.d/*.conf;|' /etc/nginx/nginx.conf
USER 101:101
ENV NGINX_ENVSUBST_OUTPUT_DIR=/tmp/conf.d
EXPOSE 8080
