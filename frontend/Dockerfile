# ─── HydroAtlas Frontend ─────────────────────────────────────────────
# Multi-stage: build with Node, serve with nginx
# ──────────────────────────────────────────────────────────────────────

# ── Stage 1: Build ───────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite inlines env vars prefixed with VITE_ at build time.
# The compose file sets VITE_API_BASE_URL so the frontend talks
# to the backend through the host, not "localhost".
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# ── Stage 2: Serve ───────────────────────────────────────────────────
FROM nginx:stable-alpine

# Copy the built assets
COPY --from=build /app/dist /usr/share/nginx/html

# Custom nginx config for SPA routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
