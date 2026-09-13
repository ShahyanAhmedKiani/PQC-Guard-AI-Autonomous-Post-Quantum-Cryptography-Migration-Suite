import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { diffLines } from 'diff';
import {
  ShieldAlert,
  ShieldCheck,
  UploadCloud,
  FileCode2,
  Terminal,
  Play,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Lightbulb,
  Download,
  Lock,
  Sparkles,
  Github,
  Search,
  Wand2,
  CheckSquare,
  Square,
  ListChecks,
  GitCompare,
  LayoutPanelLeft,
} from 'lucide-react';

const SAMPLE_CODE = `from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

def generate_session_keys():
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    return private_key, public_key

def export_public_key(public_key):
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
`;

const LEVEL_STYLES = {
  info: { icon: Terminal, color: 'text-parchment-200/70' },
  thought: { icon: Lightbulb, color: 'text-gilt-400' },
  warn: { icon: AlertTriangle, color: 'text-amber-400' },
  error: { icon: XCircle, color: 'text-rose-400' },
  success: { icon: CheckCircle2, color: 'text-emerald-400' },
};

const AGENT_COLORS = {
  Scanner: 'text-sky-300 border-sky-300/30 bg-sky-300/5',
  Refactor: 'text-gilt-400 border-gilt-400/30 bg-gilt-400/5',
  Tester: 'text-violet-300 border-violet-300/30 bg-violet-300/5',
  Orchestrator: 'text-parchment-100 border-parchment-100/25 bg-parchment-100/5',
  GithubScanner: 'text-teal-300 border-teal-300/30 bg-teal-300/5',
};

function AgentBadge({ agent }) {
  const cls = AGENT_COLORS[agent] || AGENT_COLORS.Orchestrator;
  return (
    <span className={`shrink-0 rounded-md border px-2 py-0.5 text-[11px] font-medium tracking-wide ${cls}`}>
      {agent}
    </span>
  );
}

function LogRow({ event }) {
  const style = LEVEL_STYLES[event.level] || LEVEL_STYLES.info;
  const Icon = style.icon;
  const time = new Date(event.timestamp * 1000).toLocaleTimeString([], {
    hour12: false,
    minute: '2-digit',
    second: '2-digit',
  });

  return (
    <div className="animate-fade-in-row flex items-start gap-2.5 border-b border-white/5 py-2.5 px-1 last:border-b-0">
      <span className="mt-0.5 shrink-0 font-mono text-[10.5px] text-parchment-100/35">{time}</span>
      <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${style.color}`} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <AgentBadge agent={event.agent} />
          {event.file_path && (
            <span className="shrink-0 rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-parchment-100/50">
              {event.file_path}
            </span>
          )}
          <p className={`text-[13px] leading-snug ${style.color === 'text-parchment-200/70' ? 'text-parchment-100/85' : style.color}`}>
            {event.message}
          </p>
        </div>
      </div>
    </div>
  );
}

function RiskPill({ risk }) {
  const map = {
    critical: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
    high: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    medium: 'bg-yellow-500/15 text-yellow-300 border-yellow-500/30',
    none: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  };
  return (
    <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${map[risk] || map.none}`}>
      {risk?.toUpperCase() || 'NONE'}
    </span>
  );
}

/**
 * Turns two full-text strings into an aligned list of {left, right, type}
 * rows suitable for a GitHub-style split diff: unchanged lines appear on
 * both sides, removed lines appear only on the left (right is blank), and
 * added lines appear only on the right (left is blank).
 */
function buildAlignedDiff(oldStr, refactoredStr) {
  const parts = diffLines(oldStr || '', refactoredStr || '');
  const rows = [];

  parts.forEach((part) => {
    const lines = part.value.split('\n');
    if (lines[lines.length - 1] === '') lines.pop(); // drop trailing empty from split

    if (part.added) {
      lines.forEach((line) => rows.push({ left: null, right: line, type: 'added' }));
    } else if (part.removed) {
      lines.forEach((line) => rows.push({ left: line, right: null, type: 'removed' }));
    } else {
      lines.forEach((line) => rows.push({ left: line, right: line, type: 'unchanged' }));
    }
  });

  return rows;
}

function DiffView({ originalCode, refactoredCode }) {
  const rows = useMemo(() => buildAlignedDiff(originalCode, refactoredCode), [originalCode, refactoredCode]);

  if (!refactoredCode) {
    return (
      <div className="glass-panel flex min-h-0 flex-1 items-center justify-center rounded-xl shadow-glass">
        <p className="font-mono text-[12px] text-parchment-100/25">// Run the pipeline to generate a diff</p>
      </div>
    );
  }

  const addedCount = rows.filter((r) => r.type === 'added').length;
  const removedCount = rows.filter((r) => r.type === 'removed').length;

  return (
    <div className="glass-panel flex min-h-0 flex-1 flex-col rounded-xl shadow-glass ring-1 ring-inset ring-gilt-500/20">
      <div className="flex shrink-0 items-center justify-between border-b border-white/5 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <GitCompare className="h-3.5 w-3.5 shrink-0 text-gilt-400" />
          <span className="truncate text-[12.5px] font-medium text-parchment-50">Diff</span>
          <span className="truncate text-[11px] text-parchment-100/35">original → quantum-safe</span>
        </div>
        <div className="flex shrink-0 items-center gap-3 text-[10.5px]">
          <span className="flex items-center gap-1 text-rose-300/80">
            <span className="h-1.5 w-1.5 rounded-full bg-rose-400" /> -{removedCount}
          </span>
          <span className="flex items-center gap-1 text-emerald-300/80">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> +{addedCount}
          </span>
        </div>
      </div>
      <div className="gilt-scroll min-h-0 flex-1 overflow-auto">
        {/* A single CSS grid (not two independently-stacked columns) so each
            left/right pair shares one real grid row — this keeps both sides
            vertically aligned even when a line wraps to multiple visual lines. */}
        <div className="grid grid-cols-2 divide-x divide-white/5">
          {rows.map((row, i) => (
            <React.Fragment key={i}>
              <div
                className={`whitespace-pre-wrap break-all px-3 py-0.5 font-mono text-[11.5px] leading-[1.6] ${
                  row.type === 'removed'
                    ? 'bg-rose-500/10 text-rose-200'
                    : row.left === null
                    ? 'bg-white/[0.02]'
                    : 'text-parchment-100/80'
                }`}
              >
                {row.left ?? '\u00A0'}
              </div>
              <div
                className={`whitespace-pre-wrap break-all px-3 py-0.5 font-mono text-[11.5px] leading-[1.6] ${
                  row.type === 'added'
                    ? 'bg-emerald-500/10 text-emerald-200'
                    : row.right === null
                    ? 'bg-white/[0.02]'
                    : 'text-parchment-100/80'
                }`}
              >
                {row.right ?? '\u00A0'}
              </div>
            </React.Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [filename, setFilename] = useState(null);
  const [originalCode, setOriginalCode] = useState('');
  const [refactoredCode, setRefactoredCode] = useState('');
  const [logs, setLogs] = useState([]);
  const [status, setStatus] = useState('idle'); // idle | running | verified | failed | clean
  const [scanSummary, setScanSummary] = useState(null);

  const [repoUrl, setRepoUrl] = useState('');
  const [repoScanning, setRepoScanning] = useState(false);
  const [repoFiles, setRepoFiles] = useState(null); // null = no scan yet, [] = scanned/none found
  const [selectedPaths, setSelectedPaths] = useState(new Set());
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchResults, setBatchResults] = useState(null); // [{path, final_status, final_code}, ...]
  const [batchProgress, setBatchProgress] = useState(null); // {index, total, path}
  const [viewMode, setViewMode] = useState('panels'); // 'panels' | 'diff'

  const wsRef = useRef(null);
  const repoWsRef = useRef(null);
  const batchWsRef = useRef(null);
  const terminalRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [logs]);

  const handleFileSelect = useCallback(async (file) => {
    if (!file) return;
    if (!file.name.endsWith('.py')) {
      alert('Please upload a Python (.py) file.');
      return;
    }
    const text = await file.text();
    setFilename(file.name);
    setOriginalCode(text);
    setRefactoredCode('');
    setLogs([]);
    setScanSummary(null);
    setStatus('idle');
  }, []);

  const loadSample = () => {
    setFilename('legacy_rsa_service.py');
    setOriginalCode(SAMPLE_CODE);
    setRefactoredCode('');
    setLogs([]);
    setScanSummary(null);
    setStatus('idle');
  };

  const onDrop = (e) => {
    e.preventDefault();
    const file = e.dataTransfer.files?.[0];
    handleFileSelect(file);
  };

  const runPipeline = () => {
    if (!originalCode.trim() || status === 'running') return;

    setLogs([]);
    setRefactoredCode('');
    setScanSummary(null);
    setStatus('running');

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${protocol}://${window.location.host}/ws/pipeline`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ code: originalCode }));
    };

    ws.onmessage = (msg) => {
      const event = JSON.parse(msg.data);
      setLogs((prev) => [...prev, event]);

      if (event.result) setScanSummary(event.result);
      if (event.code) setRefactoredCode(event.code);
      if (event.final_code) setRefactoredCode(event.final_code);
      if (event.final_status) setStatus(event.final_status);
    };

    ws.onerror = () => {
      setLogs((prev) => [
        ...prev,
        { id: 'ws-err', agent: 'Orchestrator', level: 'error', message: 'WebSocket connection error.', timestamp: Date.now() / 1000 },
      ]);
      setStatus('failed');
    };

    ws.onclose = () => {
      setStatus((s) => (s === 'running' ? 'failed' : s));
    };
  };

  const downloadRefactored = () => {
    const blob = new Blob([refactoredCode], { type: 'text/x-python' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename ? `pqc_${filename}` : 'pqc_refactored.py';
    a.click();
    URL.revokeObjectURL(url);
  };

  const runGithubScan = () => {
    if (!repoUrl.trim() || repoScanning) return;

    setLogs([]);
    setRepoFiles(null);
    setSelectedPaths(new Set());
    setBatchResults(null);
    setBatchProgress(null);
    setRepoScanning(true);

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${protocol}://${window.location.host}/ws/github-scan`;
    const ws = new WebSocket(wsUrl);
    repoWsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ repo_url: repoUrl }));
    };

    ws.onmessage = (msg) => {
      const event = JSON.parse(msg.data);
      setLogs((prev) => [...prev, event]);

      if (event.files !== undefined) {
        setRepoFiles(event.files || []);
        setRepoScanning(false);
      }
    };

    ws.onerror = () => {
      setLogs((prev) => [
        ...prev,
        { id: 'repo-ws-err', agent: 'GithubScanner', level: 'error', message: 'WebSocket connection error.', timestamp: Date.now() / 1000 },
      ]);
      setRepoScanning(false);
    };

    ws.onclose = () => {
      setRepoScanning(false);
    };
  };

  const migrateFile = (file) => {
    setFilename(file.path);
    setOriginalCode(file.code);
    setRefactoredCode('');
    setStatus('idle');
    setScanSummary({
      is_vulnerable: file.is_vulnerable,
      overall_risk: file.overall_risk,
      summary: file.summary,
      findings: file.findings,
    });
  };

  const toggleSelected = (path) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const selectAllVulnerable = () => {
    const vulnPaths = (repoFiles || []).filter((f) => f.is_vulnerable).map((f) => f.path);
    setSelectedPaths(new Set(vulnPaths));
  };

  const clearSelection = () => setSelectedPaths(new Set());

  const runBatchMigration = () => {
    if (selectedPaths.size === 0 || batchRunning || !repoFiles) return;

    const filesToMigrate = repoFiles
      .filter((f) => selectedPaths.has(f.path))
      .map((f) => ({ path: f.path, code: f.code }));

    setLogs([]);
    setBatchResults(null);
    setBatchProgress(null);
    setBatchRunning(true);

    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${protocol}://${window.location.host}/ws/batch-pipeline`;
    const ws = new WebSocket(wsUrl);
    batchWsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ files: filesToMigrate }));
    };

    ws.onmessage = (msg) => {
      const event = JSON.parse(msg.data);
      setLogs((prev) => [...prev, event]);

      if (event.batch_progress) setBatchProgress(event.batch_progress);
      if (event.batch_complete) {
        setBatchResults(event.results);
        setBatchRunning(false);
        setBatchProgress(null);
      }
    };

    ws.onerror = () => {
      setLogs((prev) => [
        ...prev,
        { id: 'batch-ws-err', agent: 'Orchestrator', level: 'error', message: 'WebSocket connection error.', timestamp: Date.now() / 1000 },
      ]);
      setBatchRunning(false);
    };

    ws.onclose = () => {
      setBatchRunning((r) => {
        if (r) return false;
        return r;
      });
    };
  };

  const downloadBatchFile = (result) => {
    if (!result.final_code) return;
    const blob = new Blob([result.final_code], { type: 'text/x-python' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const baseName = result.path.split('/').pop();
    a.href = url;
    a.download = `pqc_${baseName}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const statusMeta = {
    idle: { label: 'Awaiting source', color: 'text-parchment-100/50' },
    running: { label: 'Pipeline running…', color: 'text-gilt-400' },
    verified: { label: 'Verified — quantum-safe', color: 'text-emerald-400' },
    failed: { label: 'Migration failed', color: 'text-rose-400' },
    clean: { label: 'No vulnerabilities found', color: 'text-emerald-400' },
  }[status];

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      {/* Header */}
      <header className="flex shrink-0 items-center justify-between border-b border-gilt-500/15 bg-ink-950/80 px-6 py-4 backdrop-blur">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-gilt-500/30 bg-gradient-to-br from-gilt-400/20 to-transparent">
            <Lock className="h-4.5 w-4.5 text-gilt-400" />
          </div>
          <div>
            <h1 className="font-serif text-[17px] font-semibold leading-none text-parchment-50">
              PQC-Guard AI
            </h1>
            <p className="mt-1 text-[11px] leading-none text-parchment-100/45">
              Autonomous post-quantum migration console
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-[12px]">
          <span className={`h-1.5 w-1.5 rounded-full ${status === 'running' ? 'animate-pulse-soft bg-gilt-400' : 'bg-parchment-100/25'}`} />
          <span className={statusMeta.color}>{statusMeta.label}</span>
        </div>
      </header>

      {/* Split screen body */}
      <div className="flex min-h-0 flex-1 gap-4 p-4">
        {/* LEFT: upload + code panels */}
        <div className="flex w-1/2 min-w-0 flex-col gap-4">
          {/* Upload */}
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
            className="glass-panel shrink-0 rounded-xl px-5 py-4 shadow-glass"
          >
            <div className="flex items-center justify-between gap-4">
              <div className="flex min-w-0 items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-dashed border-gilt-500/40 bg-gilt-500/5">
                  <UploadCloud className="h-4.5 w-4.5 text-gilt-400" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-parchment-50">
                    {filename || 'Drop a .py file, or browse'}
                  </p>
                  <p className="text-[11px] text-parchment-100/40">Scanned locally, refactored via LLM, tested in a sandbox</p>
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  onClick={loadSample}
                  className="rounded-lg border border-gilt-500/25 px-3 py-1.5 text-[12px] font-medium text-parchment-100/70 transition hover:border-gilt-500/50 hover:text-parchment-50"
                >
                  Load sample
                </button>
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="rounded-lg bg-gradient-to-b from-gilt-400 to-gilt-600 px-3.5 py-1.5 text-[12px] font-semibold text-ink-950 shadow-gilt-glow transition hover:brightness-105"
                >
                  Browse
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".py"
                  className="hidden"
                  onChange={(e) => handleFileSelect(e.target.files?.[0])}
                />
              </div>
            </div>
          </div>

          {/* GitHub repo scan */}
          <div className="glass-panel shrink-0 rounded-xl px-5 py-4 shadow-glass">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-dashed border-gilt-500/40 bg-gilt-500/5">
                <Github className="h-4.5 w-4.5 text-gilt-400" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-parchment-50">Or scan a GitHub repository</p>
                <p className="text-[11px] text-parchment-100/40">
                  Static-scans every .py file for vulnerable crypto (no LLM call, no cost)
                </p>
              </div>
            </div>
            <div className="mt-3 flex gap-2">
              <input
                type="text"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && runGithubScan()}
                placeholder="https://github.com/owner/repo"
                className="min-w-0 flex-1 rounded-lg border border-gilt-500/20 bg-ink-950/60 px-3 py-2 text-[12.5px] text-parchment-50 placeholder:text-parchment-100/30 outline-none transition focus:border-gilt-500/50"
              />
              <button
                onClick={runGithubScan}
                disabled={!repoUrl.trim() || repoScanning}
                className="flex shrink-0 items-center gap-1.5 rounded-lg bg-gradient-to-b from-gilt-400 to-gilt-600 px-3.5 py-2 text-[12px] font-semibold text-ink-950 shadow-gilt-glow transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
              >
                {repoScanning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                Scan Repo
              </button>
            </div>

            {repoFiles && repoFiles.length > 0 && (
              <>
                <div className="mt-3 flex items-center justify-between gap-2">
                  <p className="text-[11px] text-parchment-100/40">
                    {repoFiles.filter((f) => f.is_vulnerable).length} of {repoFiles.length} file(s) flagged vulnerable
                  </p>
                  <div className="flex gap-1.5">
                    <button
                      onClick={selectAllVulnerable}
                      className="rounded-md border border-white/10 px-2 py-1 text-[10.5px] font-medium text-parchment-100/60 transition hover:border-gilt-500/40 hover:text-parchment-50"
                    >
                      Select all vulnerable
                    </button>
                    {selectedPaths.size > 0 && (
                      <button
                        onClick={clearSelection}
                        className="rounded-md border border-white/10 px-2 py-1 text-[10.5px] font-medium text-parchment-100/60 transition hover:border-white/25 hover:text-parchment-50"
                      >
                        Clear
                      </button>
                    )}
                  </div>
                </div>

                <div className="gilt-scroll mt-2 max-h-44 overflow-y-auto rounded-lg border border-white/5">
                  {repoFiles.map((f) => (
                    <div
                      key={f.path}
                      className="flex items-center justify-between gap-2 border-b border-white/5 px-3 py-2 last:border-b-0"
                    >
                      <div className="flex min-w-0 flex-1 items-center gap-2">
                        {f.is_vulnerable ? (
                          <button
                            onClick={() => toggleSelected(f.path)}
                            className="shrink-0 text-gilt-400"
                            aria-label={selectedPaths.has(f.path) ? 'Deselect' : 'Select'}
                          >
                            {selectedPaths.has(f.path) ? (
                              <CheckSquare className="h-3.5 w-3.5" />
                            ) : (
                              <Square className="h-3.5 w-3.5 text-parchment-100/30" />
                            )}
                          </button>
                        ) : (
                          <span className="w-3.5 shrink-0" />
                        )}
                        <p className="min-w-0 truncate text-[12px] text-parchment-100/85" title={f.path}>
                          {f.path}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <RiskPill risk={f.overall_risk} />
                        {f.is_vulnerable && (
                          <button
                            onClick={() => migrateFile(f)}
                            className="flex items-center gap-1 rounded-md border border-gilt-500/30 px-2 py-1 text-[11px] font-medium text-gilt-400 transition hover:bg-gilt-500/10"
                          >
                            <Wand2 className="h-3 w-3" /> Migrate
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>

                <button
                  onClick={runBatchMigration}
                  disabled={selectedPaths.size === 0 || batchRunning}
                  className="mt-2.5 flex w-full items-center justify-center gap-2 rounded-lg border border-gilt-500/30 bg-gilt-500/5 py-2 text-[12px] font-semibold text-gilt-400 transition hover:bg-gilt-500/10 disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-gilt-500/5"
                >
                  {batchRunning ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      {batchProgress
                        ? `Migrating ${batchProgress.index}/${batchProgress.total} — ${batchProgress.path}`
                        : 'Running batch migration…'}
                    </>
                  ) : (
                    <>
                      <ListChecks className="h-3.5 w-3.5" />
                      Migrate Selected ({selectedPaths.size})
                    </>
                  )}
                </button>

                {batchResults && (
                  <div className="mt-2.5 rounded-lg border border-white/5">
                    {batchResults.map((r) => (
                      <div
                        key={r.path}
                        className="flex items-center justify-between gap-2 border-b border-white/5 px-3 py-2 last:border-b-0"
                      >
                        <p className="min-w-0 flex-1 truncate text-[12px] text-parchment-100/85" title={r.path}>
                          {r.path}
                        </p>
                        <div className="flex shrink-0 items-center gap-2">
                          {r.final_status === 'verified' ? (
                            <span className="flex items-center gap-1 text-[11px] font-medium text-emerald-400">
                              <CheckCircle2 className="h-3 w-3" /> Verified
                            </span>
                          ) : (
                            <span className="flex items-center gap-1 text-[11px] font-medium text-rose-400">
                              <XCircle className="h-3 w-3" /> Failed
                            </span>
                          )}
                          {r.final_code && (
                            <button
                              onClick={() => downloadBatchFile(r)}
                              className="flex items-center gap-1 rounded-md border border-gilt-500/30 px-2 py-1 text-[11px] font-medium text-gilt-400 transition hover:bg-gilt-500/10"
                            >
                              <Download className="h-3 w-3" />
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            {repoFiles && repoFiles.length === 0 && (
              <p className="mt-3 text-[11.5px] text-parchment-100/35">
                No scannable .py files found (or the scan failed — check the terminal for details).
              </p>
            )}
          </div>

          {/* Scan summary strip */}
          {scanSummary && (
            <div className="glass-panel flex shrink-0 items-center justify-between rounded-xl px-5 py-3 shadow-glass">
              <div className="flex items-center gap-2.5">
                {scanSummary.is_vulnerable ? (
                  <ShieldAlert className="h-4 w-4 text-rose-400" />
                ) : (
                  <ShieldCheck className="h-4 w-4 text-emerald-400" />
                )}
                <p className="text-[12.5px] text-parchment-100/80">{scanSummary.summary}</p>
              </div>
              <RiskPill risk={scanSummary.overall_risk} />
            </div>
          )}

          {/* Before / After code */}
          <div className="flex shrink-0 items-center justify-end">
            <div className="flex rounded-lg border border-white/10 p-0.5">
              <button
                onClick={() => setViewMode('panels')}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition ${
                  viewMode === 'panels' ? 'bg-gilt-500/15 text-gilt-400' : 'text-parchment-100/45 hover:text-parchment-50'
                }`}
              >
                <LayoutPanelLeft className="h-3 w-3" /> Panels
              </button>
              <button
                onClick={() => setViewMode('diff')}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition ${
                  viewMode === 'diff' ? 'bg-gilt-500/15 text-gilt-400' : 'text-parchment-100/45 hover:text-parchment-50'
                }`}
              >
                <GitCompare className="h-3 w-3" /> Diff
              </button>
            </div>
          </div>

          {viewMode === 'panels' ? (
            <div className="grid min-h-0 flex-1 grid-rows-2 gap-4">
              <CodePanel
                title="Original source"
                subtitle={filename || 'untitled.py'}
                code={originalCode}
                placeholder="// Upload or paste vulnerable Python code to begin"
                icon={FileCode2}
                highlightVuln
                findings={scanSummary?.findings}
              />
              <CodePanel
                title="Quantum-safe refactor"
                subtitle="ML-KEM-512 · liboqs-python"
                code={refactoredCode}
                placeholder="// Verified PQC output will stream in here"
                icon={Sparkles}
                accent
                action={
                  refactoredCode && (
                    <button
                      onClick={downloadRefactored}
                      className="flex items-center gap-1.5 rounded-md border border-gilt-500/30 px-2.5 py-1 text-[11px] font-medium text-gilt-400 transition hover:bg-gilt-500/10"
                    >
                      <Download className="h-3 w-3" /> Download
                    </button>
                  )
                }
              />
            </div>
          ) : (
            <DiffView originalCode={originalCode} refactoredCode={refactoredCode} />
          )}

          <button
            onClick={runPipeline}
            disabled={!originalCode.trim() || status === 'running'}
            className="flex shrink-0 items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-gilt-300 to-gilt-600 py-3 text-[13.5px] font-semibold text-ink-950 shadow-gilt-glow transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
          >
            {status === 'running' ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Running pipeline…
              </>
            ) : (
              <>
                <Play className="h-4 w-4" /> Run PQC-Guard Pipeline
              </>
            )}
          </button>
        </div>

        {/* RIGHT: Agent terminal */}
        <div className="glass-panel flex w-1/2 min-w-0 flex-col rounded-xl shadow-glass">
          <div className="flex shrink-0 items-center justify-between border-b border-white/5 px-5 py-3.5">
            <div className="flex items-center gap-2.5">
              <Terminal className="h-4 w-4 text-gilt-400" />
              <h2 className="font-serif text-[14px] font-semibold text-parchment-50">Agent Terminal</h2>
            </div>
            <div className="flex items-center gap-3 text-[10.5px] text-parchment-100/40">
              <LegendDot color="bg-sky-300" label="Scanner" />
              <LegendDot color="bg-gilt-400" label="Refactor" />
              <LegendDot color="bg-violet-300" label="Tester" />
              <LegendDot color="bg-teal-300" label="GitHub" />
            </div>
          </div>
          <div ref={terminalRef} className="gilt-scroll min-h-0 flex-1 overflow-y-auto px-3 py-2">
            {logs.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-parchment-100/30">
                <Terminal className="h-6 w-6" />
                <p className="text-[12.5px]">Agent thoughts and execution logs will stream here.</p>
              </div>
            ) : (
              logs.map((event, i) => <LogRow key={event.id || i} event={event} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function LegendDot({ color, label }) {
  return (
    <span className="flex items-center gap-1">
      <span className={`h-1.5 w-1.5 rounded-full ${color}`} />
      {label}
    </span>
  );
}

function CodePanel({ title, subtitle, code, placeholder, icon: Icon, accent, highlightVuln, findings, action }) {
  const vulnLines = new Set((findings || []).map((f) => f.line_number));

  return (
    <div
      className={`glass-panel flex min-h-0 flex-col rounded-xl shadow-glass ${
        accent ? 'ring-1 ring-inset ring-gilt-500/20' : ''
      }`}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-white/5 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className={`h-3.5 w-3.5 shrink-0 ${accent ? 'text-gilt-400' : 'text-parchment-100/50'}`} />
          <span className="truncate text-[12.5px] font-medium text-parchment-50">{title}</span>
          <span className="truncate text-[11px] text-parchment-100/35">{subtitle}</span>
        </div>
        {action}
      </div>
      <div className="gilt-scroll min-h-0 flex-1 overflow-auto px-4 py-3">
        {code ? (
          <pre className="font-mono text-[12px] leading-[1.65]">
            {code.split('\n').map((line, idx) => {
              const lineNo = idx + 1;
              const isVuln = highlightVuln && vulnLines.has(lineNo);
              return (
                <div
                  key={idx}
                  className={`flex gap-3 rounded px-1.5 ${isVuln ? 'bg-rose-500/10 text-rose-200' : 'text-parchment-100/85'}`}
                >
                  <span className="w-6 shrink-0 select-none text-right text-parchment-100/25">{lineNo}</span>
                  <span className="whitespace-pre-wrap break-all">{line || ' '}</span>
                </div>
              );
            })}
          </pre>
        ) : (
          <p className="font-mono text-[12px] text-parchment-100/25">{placeholder}</p>
        )}
      </div>
    </div>
  );
}
