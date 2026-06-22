import { tool } from '@opencode-ai/plugin';
import { existsSync, mkdirSync, writeFileSync } from 'fs';
import * as path from 'path';

const ROOT = process.cwd();

const writeIfMissing = (file: string, content: string): boolean => {
    if (existsSync(file)) return false;
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, content);
    return true;
};

const summarize = (created: string[], skipped: string[]): string => {
    const c = created.length ? created.join(', ') : '(none)';
    const s = skipped.length ? skipped.join(', ') : '(none)';
    return `Created: ${c}\nSkipped (already present): ${s}`;
};

export const init_data_engine = tool({
    description:
        'Generate the NestJS data-engine skeleton (package.json, tsconfig.json, nest-cli.json, .env.example, src/main.ts, src/app.module.ts). Idempotent: skips files that already exist.',
    args: {},
    async execute() {
        const base = path.join(ROOT, 'apps/data-engine');
        const created: string[] = [];
        const skipped: string[] = [];

        const pkg = {
            name: '@argos/data-engine',
            version: '0.0.1',
            private: true,
            scripts: {
                build: 'nest build',
                start: 'nest start',
                'start:dev': 'nest start --watch',
                test: 'jest',
                'test:watch': 'jest --watch',
                lint: 'eslint "src/**/*.ts"',
                typecheck: 'tsc --noEmit',
            },
            dependencies: {
                '@nestjs/common': '^10.0.0',
                '@nestjs/core': '^10.0.0',
                '@nestjs/microservices': '^10.0.0',
                '@nestjs/platform-express': '^10.0.0',
                ioredis: '^5.3.2',
                ws: '^8.16.0',
                reflect: '0.2.0',
                rxjs: '^7.8.1',
            },
            devDependencies: {
                '@nestjs/cli': '^10.0.0',
                '@nestjs/schematics': '^10.0.0',
                '@types/express': '^4.17.21',
                '@types/jest': '^29.5.11',
                '@types/node': '^20.10.0',
                '@types/ws': '^8.5.10',
                '@typescript-eslint/eslint-plugin': '^6.13.0',
                '@typescript-eslint/parser': '^6.13.0',
                eslint: '^8.55.0',
                jest: '^29.7.0',
                'ts-jest': '^29.1.1',
                typescript: '^5.3.0',
            },
            jest: {
                preset: 'ts-jest',
                testEnvironment: 'node',
                rootDir: 'src',
                testRegex: '.*\\.spec\\.ts$',
            },
        };

        const tsconfig = {
            compilerOptions: {
                module: 'commonjs',
                target: 'ES2022',
                lib: ['ES2022'],
                outDir: './dist',
                rootDir: './src',
                baseUrl: './',
                strict: true,
                noImplicitAny: true,
                strictNullChecks: true,
                experimentalDecorators: true,
                emitDecoratorMetadata: true,
                esModuleInterop: true,
                skipLibCheck: true,
                forceConsistentCasingInFileNames: true,
                resolveJsonModule: true,
                declaration: true,
                sourceMap: true,
            },
            include: ['src/**/*'],
            exclude: ['node_modules', 'dist'],
        };

        const nestCli = {
            collection: '@nestjs/schematics',
            sourceRoot: 'src',
            compilerOptions: { deleteOutDir: true },
        };

        const envExample =
            '# Data Engine (NestJS) - copy to .env, do not commit\n' +
            '# Broker is any RESP-compatible implementation:\n' +
            '#   Redis 7+ / Memurai (Windows) / Dragonfly / Valkey / KeyDB / Garnet / Redict\n' +
            'ARGOS_BROKER_URL=redis://localhost:6379\n' +
            'ENVIRONMENT_MODE=PAPER_TRADING\n' +
            'EXCHANGE_WS_URL=wss://stream.binance.com:9443/ws\n' +
            'SYMBOL=BTC/USDT\n' +
            'TIMEFRAME=1m\n';

        const mainTs =
            'import "reflect-metadata"\n' +
            'import { NestFactory } from "@nestjs/core"\n' +
            'import { AppModule } from "./app.module"\n' +
            '\n' +
            'async function bootstrap(): Promise<void> {\n' +
            '  const app = await NestFactory.create(AppModule)\n' +
            '  await app.listen(3000)\n' +
            '  // eslint-disable-next-line no-console\n' +
            '  console.log("[data-engine] listening on :3000")\n' +
            '}\n' +
            'bootstrap().catch((err) => {\n' +
            '  // eslint-disable-next-line no-console\n' +
            '  console.error("[data-engine] fatal init error", err)\n' +
            '  process.exit(1)\n' +
            '})\n';

        const appModuleTs =
            'import { Module } from "@nestjs/common"\n' + '\n' + '@Module({})\n' + 'export class AppModule {}\n';

        const files: Record<string, string> = {
            'package.json': JSON.stringify(pkg, null, 2) + '\n',
            'tsconfig.json': JSON.stringify(tsconfig, null, 2) + '\n',
            'nest-cli.json': JSON.stringify(nestCli, null, 2) + '\n',
            '.env.example': envExample,
            'src/main.ts': mainTs,
            'src/app.module.ts': appModuleTs,
        };

        for (const [rel, content] of Object.entries(files)) {
            const abs = path.join(base, rel);
            if (writeIfMissing(abs, content)) created.push(rel);
            else skipped.push(rel);
        }

        return { title: 'init_data_engine', output: summarize(created, skipped) };
    },
});

export const init_analytics_engine = tool({
    description:
        'Generate the FastAPI analytics-engine skeleton (pyproject.toml, requirements.txt, .env.example, app/main.py, package __init__.py files, a basic health test). Idempotent.',
    args: {},
    async execute() {
        const base = path.join(ROOT, 'apps/analytics-engine');
        const created: string[] = [];
        const skipped: string[] = [];

        const pyproject =
            '[project]\n' +
            'name = "argos-analytics-engine"\n' +
            'version = "0.0.1"\n' +
            'description = "Argos ATS - analytics & IA engine (FastAPI)."\n' +
            'requires-python = ">=3.11"\n' +
            'dependencies = [\n' +
            '    "fastapi>=0.110",\n' +
            '    "uvicorn[standard]>=0.27",\n' +
            '    "pandas>=2.1",\n' +
            '    "numpy>=1.26",\n' +
            '    "ta>=0.11",\n' +
            '    "ccxt>=4.1",\n' +
            '    "redis>=5.0",\n' +
            '    "pydantic>=2.6",\n' +
            '    "pydantic-settings>=2.2",\n' +
            '    "python-dotenv>=1.0",\n' +
            '    "structlog>=24.1",\n' +
            ']\n' +
            '\n' +
            '[project.optional-dependencies]\n' +
            'ml = ["tensorflow>=2.15", "torch>=2.1"]\n' +
            '\n' +
            '[tool.pytest.ini_options]\n' +
            'testpaths = ["app"]\n' +
            'python_files = ["test_*.py", "*_test.py"]\n' +
            'addopts = "-x --tb=short"\n';

        const requirements =
            'fastapi>=0.110\n' +
            'uvicorn[standard]>=0.27\n' +
            'pandas>=2.1\n' +
            'numpy>=1.26\n' +
            'ta>=0.11\n' +
            'ccxt>=4.1\n' +
            'redis>=5.0\n' +
            'pydantic>=2.6\n' +
            'pydantic-settings>=2.2\n' +
            'python-dotenv>=1.0\n' +
            'structlog>=24.1\n';

        const envExample =
            '# Analytics Engine (FastAPI) - copy to .env, do not commit\n' +
            '# Broker is any RESP-compatible implementation:\n' +
            '#   Redis 7+ / Memurai (Windows) / Dragonfly / Valkey / KeyDB / Garnet / Redict\n' +
            'ARGOS_BROKER_URL=redis://localhost:6379\n' +
            'ENVIRONMENT_MODE=PAPER_TRADING\n' +
            'RISK_PCT=0.01\n' +
            'DRAWDOWN_PCT=0.05\n' +
            'EXCHANGE_API_KEY=\n' +
            'EXCHANGE_API_SECRET=\n' +
            'EXCHANGE_PASSPHRASE=\n';

        const mainPy =
            '"""Argos analytics & IA engine entry point."""\n' +
            'import os\n' +
            'import structlog\n' +
            'from fastapi import FastAPI\n' +
            '\n' +
            'log = structlog.get_logger()\n' +
            'app = FastAPI(title="argos-analytics-engine", version="0.0.1")\n' +
            '\n' +
            '\n' +
            '@app.get("/health")\n' +
            'async def health() -> dict:\n' +
            '    return {"status": "ok", "mode": os.environ.get("ENVIRONMENT_MODE", "PAPER_TRADING")}\n' +
            '\n' +
            '\n' +
            'if __name__ == "__main__":\n' +
            '    import uvicorn\n' +
            '    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)\n';

        const healthTest =
            'from fastapi.testclient import TestClient\n' +
            'from app.main import app\n' +
            '\n' +
            'client = TestClient(app)\n' +
            '\n' +
            '\n' +
            'def test_health() -> None:\n' +
            '    r = client.get("/health")\n' +
            '    assert r.status_code == 200\n' +
            '    assert r.json()["status"] == "ok"\n';

        const files: Record<string, string> = {
            'pyproject.toml': pyproject,
            'requirements.txt': requirements,
            '.env.example': envExample,
            'app/__init__.py': '',
            'app/main.py': mainPy,
            'app/domain/__init__.py': '',
            'app/application/__init__.py': '',
            'app/infrastructure/__init__.py': '',
            'app/strategies/__init__.py': '',
            'tests/__init__.py': '',
            'tests/test_health.py': healthTest,
        };

        for (const [rel, content] of Object.entries(files)) {
            const abs = path.join(base, rel);
            if (writeIfMissing(abs, content)) created.push(rel);
            else skipped.push(rel);
        }

        return {
            title: 'init_analytics_engine',
            output: summarize(created, skipped),
        };
    },
});

export const init_compose = tool({
    description:
        'Generate docker-compose.yml with data-engine, analytics-engine, and a RESP-compatible broker (defaults to redis:7-alpine; can be swapped for memurai, valkey, dragonfly, etc. via the `broker` service override). Includes healthchecks, isolated network, named volume. Docker is ONE deployment option — bare-metal is also supported. Idempotent.',
    args: {},
    async execute() {
        const file = path.join(ROOT, 'docker-compose.yml');
        if (existsSync(file))
            return {
                title: 'init_compose',
                output: 'Skipped (already present): docker-compose.yml',
            };

        const compose =
            '# Argos ATS - production-grade Automated Trading System stack\n' +
            '# Spec: spec.md sections 1 and 4 (OWASP 4-phase incident response)\n' +
            '#\n' +
            '# Broker is pluggable. Default uses redis:7-alpine. To use another\n' +
            '# RESP-compatible broker (memurai, valkey, dragonfly, keydb, ...),\n' +
            '# override the `broker` service: change `image:` to your image and\n' +
            '# adjust the healthcheck. Application code reads ARGOS_BROKER_URL\n' +
            '# and is broker-agnostic.\n' +
            'services:\n' +
            '  data-engine:\n' +
            '    build: ./apps/data-engine\n' +
            '    container_name: argos-data-engine\n' +
            '    environment:\n' +
            '      - ENVIRONMENT_MODE=${ENVIRONMENT_MODE:-PAPER_TRADING}\n' +
            '      - ARGOS_BROKER_URL=redis://broker:6379\n' +
            '      - EXCHANGE_WS_URL=${EXCHANGE_WS_URL:-wss://stream.binance.com:9443/ws}\n' +
            '    env_file:\n' +
            '      - .env\n' +
            '    depends_on:\n' +
            '      broker:\n' +
            '        condition: service_healthy\n' +
            '    networks: [argos-net]\n' +
            '    restart: unless-stopped\n' +
            '    healthcheck:\n' +
            '      test: ["CMD", "node", "-e", "require(\'http\').get(\'http://localhost:3000/health\', r => process.exit(r.statusCode === 200 ? 0 : 1)).on(\'error\', () => process.exit(1))"]\n' +
            '      interval: 30s\n' +
            '      timeout: 5s\n' +
            '      retries: 3\n' +
            '      start_period: 20s\n' +
            '    logging:\n' +
            '      driver: json-file\n' +
            '      options: { max-size: "10m", max-file: "3" }\n' +
            '\n' +
            '  analytics-engine:\n' +
            '    build: ./apps/analytics-engine\n' +
            '    container_name: argos-analytics-engine\n' +
            '    environment:\n' +
            '      - ENVIRONMENT_MODE=${ENVIRONMENT_MODE:-PAPER_TRADING}\n' +
            '      - ARGOS_BROKER_URL=redis://broker:6379\n' +
            '      - RISK_PCT=${RISK_PCT:-0.01}\n' +
            '      - DRAWDOWN_PCT=${DRAWDOWN_PCT:-0.05}\n' +
            '    env_file:\n' +
            '      - .env\n' +
            '    depends_on:\n' +
            '      broker:\n' +
            '        condition: service_healthy\n' +
            '    networks: [argos-net]\n' +
            '    restart: unless-stopped\n' +
            '    healthcheck:\n' +
            '      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen(\'http://localhost:8000/health\')"]\n' +
            '      interval: 30s\n' +
            '      timeout: 5s\n' +
            '      retries: 3\n' +
            '      start_period: 20s\n' +
            '    logging:\n' +
            '      driver: json-file\n' +
            '      options: { max-size: "10m", max-file: "3" }\n' +
            '\n' +
            '  # RESP-compatible broker. Swap image to use another implementation.\n' +
            '  # Examples: redis:7-alpine | valkey/valkey:7-alpine | dhrdldh3/dragonfly |\n' +
            '  #           eqalpha/keydb | ghcr.io/microsoft/garnet\n' +
            '  broker:\n' +
            '    image: redis:7-alpine\n' +
            '    container_name: argos-broker\n' +
            '    command: ["redis-server", "--appendonly", "yes"]\n' +
            '    networks: [argos-net]\n' +
            '    volumes:\n' +
            '      - broker-data:/data\n' +
            '    restart: unless-stopped\n' +
            '    healthcheck:\n' +
            '      test: ["CMD", "redis-cli", "ping"]\n' +
            '      interval: 10s\n' +
            '      timeout: 3s\n' +
            '      retries: 3\n' +
            '      start_period: 5s\n' +
            '\n' +
            'networks:\n' +
            '  argos-net:\n' +
            '    driver: bridge\n' +
            '\n' +
            'volumes:\n' +
            '  broker-data:\n';

        writeFileSync(file, compose);
        return {
            title: 'init_compose',
            output: 'Created: docker-compose.yml',
        };
    },
});

export const init_git = tool({
    description:
        'Initialize a git repository and write a comprehensive .gitignore (node_modules, __pycache__, .env, .venv, dist, .opencode/cache, secrets, IDE artefacts). Idempotent. Pre-requisite for AGENTS.md §12 Git workflow.',
    args: {},
    async execute(_args, ctx) {
        const gitDir = path.join(ROOT, '.git');
        const gitignore = path.join(ROOT, '.gitignore');

        const created: string[] = [];
        const skipped: string[] = [];

        if (existsSync(gitDir)) skipped.push('.git (already initialized)');
        else {
            const { execFileSync } = await import('child_process');
            execFileSync('git', ['init'], { stdio: 'ignore' });
            created.push('.git (git init)');
        }

        if (existsSync(gitignore)) skipped.push('.gitignore');
        else {
            const content =
                '# Node / TypeScript\n' +
                'node_modules/\n' +
                'dist/\n' +
                'coverage/\n' +
                '*.log\n' +
                'npm-debug.log*\n' +
                '\n' +
                '# Python\n' +
                '__pycache__/\n' +
                '*.py[cod]\n' +
                '*$py.class\n' +
                '.pytest_cache/\n' +
                '.venv/\n' +
                'venv/\n' +
                'env/\n' +
                '.mypy_cache/\n' +
                '.ruff_cache/\n' +
                '\n' +
                '# Secrets & env\n' +
                '.env\n' +
                '.env.local\n' +
                '.env.*.local\n' +
                '\n' +
                '# IDE / OS\n' +
                '.idea/\n' +
                '.vscode/\n' +
                '*.swp\n' +
                '.DS_Store\n' +
                'Thumbs.db\n' +
                '\n' +
                '# opencode cache\n' +
                '.opencode/cache/\n' +
                '\n' +
                '# Project artefacts\n' +
                'data/*.csv\n' +
                '!data/.gitkeep\n' +
                'backtest_results/\n';
            writeFileSync(gitignore, content);
            created.push('.gitignore');
        }

        await ctx.metadata({ title: 'init_git', metadata: { created, skipped } });
        return {
            title: 'init_git',
            output: `Created: ${created.join(', ') || '(none)'}\nSkipped: ${skipped.join(', ') || '(none)'}`,
        };
    },
});

export const init_config = tool({
    description:
        'Generate config.json at the project root with sensible defaults from spec.md. Schema is broker-agnostic: `broker.kind` selects the protocol family (default "redis-protocol" covers Redis, Memurai, Dragonfly, Valkey, etc.). Idempotent.',
    args: {},
    async execute() {
        const file = path.join(ROOT, 'config.json');
        if (existsSync(file))
            return {
                title: 'init_config',
                output: 'Skipped (already present): config.json',
            };

        const cfg = {
            environment_mode: 'PAPER_TRADING',
            risk_pct: 0.01,
            drawdown_pct: 0.05,
            broker: {
                kind: 'redis-protocol',
                url: '${ARGOS_BROKER_URL}',
                stream_prefix: 'ticks:',
            },
            exchanges: [
                {
                    id: 'binance',
                    type: 'perpetual_futures',
                    testnet: true,
                },
            ],
            symbols: ['BTC/USDT'],
            timeframes: ['1m', '5m', '15m', '1h', '4h', '1d'],
        };
        writeFileSync(file, JSON.stringify(cfg, null, 2) + '\n');
        return { title: 'init_config', output: 'Created: config.json' };
    },
});
