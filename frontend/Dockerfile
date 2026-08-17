# Dev-образ: Vite dev-server. HMR работает при смонтированных исходниках — см.
# docker-compose.yml. Прод-сборка приезжает в спринте 5 (Vercel).
FROM node:22-bookworm-slim

ENV PNPM_HOME="/pnpm" \
    PATH="/pnpm:$PATH"

RUN corepack enable

WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .

# /pnpm существует только внутри шага с cache-mount'ом — здесь его нужно создать заново.
RUN mkdir -p /pnpm && chown -R node:node /app /pnpm
USER node

EXPOSE 5173

CMD ["pnpm", "dev"]
