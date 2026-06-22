import { tool } from '@opencode-ai/plugin';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { randomUUID } from 'crypto';

const JSON_PATH = process.cwd() + '/SESSION.json';
const SID_FILE = process.cwd() + '/.session_id_' + process.pid;

interface LockEntry {
    path: string;
    change: string;
    detail: string;
    scope: string;
    status: 'in_progress' | 'done' | 'verified' | 'superseded';
    locked_by: string;
    locked_at: string;
    unlocked_at: string | null;
    needs_review: boolean;
}

interface DecisionEntry {
    id: string;
    timestamp: string;
    scope: string;
    decision: string;
    rationale: string;
    alternative: string;
    status: 'pending' | 'applied' | 'superseded' | 'rejected';
}

interface SessionEntry {
    created: string;
    locked_branch: string;
    locked_files: LockEntry[];
    decisions: DecisionEntry[];
    notes: string;
}

interface SessionState {
    sessions: Record<string, SessionEntry>;
}

function defaultState(): SessionState {
    return { sessions: {} };
}

function readSession(): SessionState {
    if (!existsSync(JSON_PATH)) return defaultState();
    try {
        const raw = readFileSync(JSON_PATH, 'utf-8');
        const parsed = JSON.parse(raw) as Record<string, unknown>;
        // Migrate old format: { session_id, locked_files, ... } -> { sessions: { [id]: {...} } }
        if (parsed.session_id && typeof parsed.session_id === 'string' && !parsed.sessions) {
            const entry: SessionEntry = {
                created: (parsed.created as string) || '',
                locked_branch: (parsed.locked_branch as string) || '',
                locked_files: (parsed.locked_files as LockEntry[]) || [],
                decisions: (parsed.decisions as DecisionEntry[]) || [],
                notes: (parsed.notes as string) || '',
            };
            return { sessions: { [parsed.session_id as string]: entry } };
        }
        return parsed as SessionState;
    } catch {
        return defaultState();
    }
}

function writeSession(state: SessionState): void {
    writeFileSync(JSON_PATH, JSON.stringify(state, null, 2), 'utf-8');
}

function getMySessionId(): string {
    if (existsSync(SID_FILE)) return readFileSync(SID_FILE, 'utf-8').trim();
    return '';
}

function setMySessionId(uuid: string): void {
    writeFileSync(SID_FILE, uuid, 'utf-8');
}

function getOrCreateMySessionId(): string {
    let sid = getMySessionId();
    if (!sid) {
        sid = randomUUID();
        setMySessionId(sid);
    }
    return sid;
}

function assertMySessionId(): string {
    const sid = getMySessionId();
    if (!sid) throw new Error('No hay sesión activa. Ejecutá session_begin() primero.');
    return sid;
}

function ensureMyEntry(state: SessionState, branch: string): string {
    const sid = getOrCreateMySessionId();
    if (!state.sessions[sid]) {
        state.sessions[sid] = {
            created: new Date().toISOString(),
            locked_branch: branch,
            locked_files: [],
            decisions: [],
            notes: '',
        };
    }
    return sid;
}

// ── Tools ──────────────────────────────────────

export const session_begin = tool({
    description:
        'Inicializa (o reanuda) una sesión. Asigna UUID persistente por proceso, añade entrada ' +
        'al mapa de sesiones en SESSION.json. Si la sesión ya existe, la reanuda.',
    args: {
        branch: tool.schema.string().describe('Git branch actual (feature/..., fix/..., etc.)'),
    },
    async execute({ branch }) {
        const state = readSession();
        const sid = getOrCreateMySessionId();
        const existing = state.sessions[sid];

        if (existing) {
            // Session ya existe — reanudar
            const active = existing.locked_files.filter((f) => f.status === 'in_progress');
            if (active.length > 0) {
                return {
                    title: '⚠️  Sesión reanudada con archivos activos',
                    output: [
                        `Session: ${sid.slice(0, 8)} (reanudada)`,
                        `Branch: ${branch}`,
                        `Archivos bloqueados (${active.length}):`,
                        ...active.map(
                            (f) => `  • ${f.path} — "${f.change}" (scope: ${f.scope}, desde ${f.locked_at})`,
                        ),
                        '',
                        'Verifica que no estés pisando cambios de otra instancia.',
                    ].join('\n'),
                };
            }
            return {
                title: '✅ Sesión reanudada',
                output: `session_id: ${sid}\nbranch: ${branch}\ncreated: ${existing.created}`,
            };
        }

        // Nueva sesión
        state.sessions[sid] = {
            created: new Date().toISOString(),
            locked_branch: branch,
            locked_files: [],
            decisions: [],
            notes: '',
        };
        writeSession(state);

        return {
            title: '✅ Sesión iniciada',
            output: `session_id: ${sid}\nbranch: ${branch}\ncreated: ${new Date().toISOString()}`,
        };
    },
});

export const session_lock = tool({
    description:
        'Bloquea un archivo para editarlo. Si otra sesión ya lo tiene con el mismo scope, ' +
        'informa que ya está cubierto. Si es distinto scope, registra posible conflicto.',
    args: {
        path: tool.schema.string().describe('Ruta del archivo (relativa al repo root)'),
        change: tool.schema.string().describe('Descripción corta del cambio'),
        detail: tool.schema.string().describe('Detalle del cambio'),
        scope: tool.schema.string().describe('Categoría semántica (logging, validation, security, ...)'),
    },
    async execute({ path, change, detail, scope }) {
        const state = readSession();
        const now = new Date().toISOString();
        const mySid = assertMySessionId();

        // Buscar lock activo de OTRA sesión sobre el mismo path
        for (const [otherSid, entry] of Object.entries(state.sessions)) {
            if (otherSid === mySid) continue;
            const existing = entry.locked_files.find(
                (f) => f.path === path && f.status === 'in_progress',
            );
            if (existing) {
                if (existing.scope === scope) {
                    return {
                        title: '⏭️  Archivo ya cubierto por otra sesión',
                        output: `"${path}" bloqueado por ${otherSid.slice(0, 8)} con scope "${scope}":\n  "${existing.change}"\n\nSaltando.`,
                    };
                }
                return {
                    title: '⚠️  Conflicto potencial con otra sesión',
                    output: [
                        `"${path}" bloqueado por ${otherSid.slice(0, 8)} con scope DISTINTO:`,
                        `  Ellos: "${existing.change}" (scope: ${existing.scope})`,
                        `  Tú:    "${change}" (scope: ${scope})`,
                        '',
                        'Registrando conflicto. Verifica al final.',
                    ].join('\n'),
                };
            }
        }

        // Asegurar que mi entry existe
        const myBranch = state.sessions[mySid]?.locked_branch || 'dev';
        ensureMyEntry(state, myBranch);

        // Remover done previo del mismo path
        state.sessions[mySid].locked_files = state.sessions[mySid].locked_files.filter(
            (f) => !(f.path === path && f.status === 'done'),
        );
        state.sessions[mySid].locked_files.push({
            path,
            change,
            detail,
            scope,
            status: 'in_progress',
            locked_by: mySid,
            locked_at: now,
            unlocked_at: null,
            needs_review: false,
        });

        writeSession(state);
        return {
            title: '🔒 Archivo bloqueado',
            output: `${path}\nchange: ${change}\nscope: ${scope}\nlocked_by: ${mySid.slice(0, 8)}\nlocked_at: ${now}`,
        };
    },
});

export const session_unlock = tool({
    description: 'Libera un archivo. Cambia su estado a done (default), verified, o superseded.',
    args: {
        path: tool.schema.string().describe('Ruta del archivo a liberar'),
        status: tool.schema
            .string()
            .describe('Estado final: done (default), verified, superseded')
            .default('done'),
        needs_review: tool.schema
            .boolean()
            .describe('true si querés que otra sesión verifique')
            .default(false),
    },
    async execute({ path, status, needs_review }) {
        const state = readSession();
        const mySid = assertMySessionId();
        const myFiles = state.sessions[mySid]?.locked_files;
        if (!myFiles) {
            return {
                title: '⚠️  No encontrado',
                output: `"${path}" no está bloqueado por esta sesión.`,
            };
        }

        const idx = myFiles.findIndex(
            (f) => f.path === path && f.status === 'in_progress' && f.locked_by === mySid,
        );
        if (idx === -1) {
            return {
                title: '⚠️  No encontrado',
                output: `"${path}" no está en estado in_progress para esta sesión.`,
            };
        }

        state.sessions[mySid].locked_files[idx] = {
            ...state.sessions[mySid].locked_files[idx],
            status: status as LockEntry['status'],
            unlocked_at: new Date().toISOString(),
            needs_review,
        };
        writeSession(state);
        return {
            title: '🔓 Archivo liberado',
            output: `${path}\nstatus: ${status}\nneeds_review: ${needs_review}`,
        };
    },
});

export const session_status = tool({
    description:
        'Muestra el estado actual del semáforo: todas las sesiones activas, archivos bloqueados, decisiones pendientes.',
    args: {},
    async execute() {
        const state = readSession();
        const mySid = getMySessionId();
        const lines: string[] = [];
        const entries = Object.entries(state.sessions);

        if (entries.length === 0) {
            return {
                title: '📋 Session — sin sesiones activas',
                output: 'No hay sesiones registradas. Ejecutá session_begin() para iniciar una.',
            };
        }

        const myLabel = mySid ? ` (tú → ${mySid.slice(0, 8)})` : '';
        lines.push(`📋 Sesiones activas: ${entries.length}${myLabel}\n`);

        for (const [sid, entry] of entries) {
            const isMine = sid === mySid;
            const prefix = isMine ? ' ▶' : '  ';
            lines.push(`${prefix} [${sid.slice(0, 8)}] branch: ${entry.locked_branch}`);
            lines.push(`     creada: ${entry.created || 'N/A'}`);

            const inProgress = entry.locked_files.filter((f) => f.status === 'in_progress');
            const done = entry.locked_files.filter((f) => f.status === 'done');
            const verified = entry.locked_files.filter((f) => f.status === 'verified');

            if (inProgress.length > 0) {
                lines.push(`     🔴 In progress (${inProgress.length}):`);
                for (const f of inProgress) {
                    lines.push(`        • ${f.path}`);
                    lines.push(`          "${f.change}" [${f.scope}]`);
                }
            }
            if (done.length > 0) {
                lines.push(`     🟢 Done (${done.length}):`);
                for (const f of done) {
                    lines.push(
                        `        • ${f.path} ${f.needs_review ? '⚠️ needs review' : '✅'}`,
                    );
                }
            }
            if (verified.length > 0) {
                lines.push(`     ✅ Verified (${verified.length}):`);
                for (const f of verified) lines.push(`        • ${f.path}`);
            }

            const pending = entry.decisions.filter((d) => d.status === 'pending');
            if (pending.length > 0) {
                lines.push(`     📝 Decisiones pendientes (${pending.length}):`);
                for (const d of pending) lines.push(`        • ${d.id}: ${d.decision}`);
            }

            if (entry.notes) lines.push(`     📓 Notes: ${entry.notes}`);
            lines.push('');
        }

        return {
            title: `Session — ${entries.length} sesiones`,
            output: lines.join('\n'),
        };
    },
});

export const session_decision = tool({
    description: 'Registra una decisión de diseño. Llamar ANTES de implementar cambios no triviales.',
    args: {
        scope: tool.schema.string().describe('Archivo/módulo afectado'),
        decision: tool.schema.string().describe('La decisión en sí'),
        rationale: tool.schema.string().describe('Por qué se tomó'),
        alternative: tool.schema.string().describe('Alternativa descartada (opcional)').default(''),
    },
    async execute({ scope, decision, rationale, alternative }) {
        const state = readSession();
        const mySid = assertMySessionId();
        const myBranch = state.sessions[mySid]?.locked_branch || 'dev';
        ensureMyEntry(state, myBranch);

        const decisions = state.sessions[mySid].decisions;
        const entry: DecisionEntry = {
            id: `d-${String(decisions.length + 1).padStart(3, '0')}`,
            timestamp: new Date().toISOString(),
            scope,
            decision,
            rationale,
            alternative,
            status: 'pending',
        };
        decisions.push(entry);
        writeSession(state);
        return {
            title: '📝 Decisión registrada',
            output: `${entry.id} | ${scope}\n${decision}\nRationale: ${rationale}\n${alternative ? 'Alternative: ' + alternative : ''}`,
        };
    },
});
