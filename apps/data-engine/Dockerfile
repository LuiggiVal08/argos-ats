FROM node:24-slim
WORKDIR /app

RUN groupadd --system argos && useradd --system --gid argos argos && \
    mkdir -p /app/data && chown argos:argos /app/data

COPY apps/data-engine/package*.json apps/data-engine/tsconfig.json apps/data-engine/nest-cli.json apps/data-engine/
RUN cd apps/data-engine && npm ci --ignore-scripts

# better-sqlite3 has no prebuilt for musl; copy host-compiled binary
COPY apps/data-engine/node_modules/better-sqlite3/build/Release/better_sqlite3.node \
    apps/data-engine/node_modules/better-sqlite3/build/Release/

COPY apps/data-engine/src/ apps/data-engine/src/
COPY contracts/ contracts/
RUN cd apps/data-engine && npm run build && npm prune --omit=dev

USER argos
EXPOSE 3000
ENV NODE_ENV=production
CMD ["node", "apps/data-engine/dist/main.js"]
