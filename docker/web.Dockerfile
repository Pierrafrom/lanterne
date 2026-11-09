# Multi-stage build — Next.js standalone output (nextjs.org/docs/app/
# api-reference/config/next-config-js/output), same non-root pattern as
# every other image in this repo.
FROM node:22-alpine AS builder

WORKDIR /app

# pnpm-workspace.yaml carries the project's approve-builds decision (sharp's
# native install script) — without it, this install step fails the same way
# a bare `pnpm install` does locally before running `pnpm approve-builds`.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && corepack prepare pnpm@latest --activate \
    && pnpm install --frozen-lockfile

COPY . .
RUN pnpm run build

FROM node:22-alpine
WORKDIR /app

RUN adduser -D webuser
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/static ./.next/static
RUN chown -R webuser:webuser /app
USER webuser

# Next.js standalone's server.js binds to localhost only unless HOSTNAME is
# explicitly 0.0.0.0 — see docs/decisions/0018-postgres-gui-client-pgweb.md
# for the same gotcha hit while building the (unused) Outerbase Studio image.
ENV HOSTNAME="0.0.0.0"
ENV PORT=3000
EXPOSE 3000

CMD ["node", "server.js"]
