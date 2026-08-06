# syntax=docker/dockerfile:1.7
# Multi-stage build for the Next.js 16 (App Router) frontend, output=standalone.
#
# NEXT_PUBLIC_KABIL_API is a *build-time* value: Next.js inlines NEXT_PUBLIC_*
# into the client bundle at build, so it must be passed as a build arg (the
# deploy script / compose supplies it). Changing the API URL requires a rebuild.

# ---------- deps ----------
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci

# ---------- builder ----------
FROM node:22-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_KABIL_API
ENV NEXT_PUBLIC_KABIL_API=$NEXT_PUBLIC_KABIL_API
# Cap the Node heap so `next build` doesn't OOM on the small VM (leans on swap).
ENV NODE_OPTIONS=--max-old-space-size=1536
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---------- runtime ----------
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs
# standalone bundle = server.js + only the node_modules it actually needs.
# Static assets and public/ are NOT included in standalone — copy them in.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
