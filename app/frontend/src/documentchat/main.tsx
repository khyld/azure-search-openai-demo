import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import {
    ArrowDownload24Regular,
    ArrowReset24Regular,
    ArrowUp24Regular,
    ArrowUpload24Regular,
    Checkmark24Regular,
    Dismiss24Regular,
    DocumentText24Regular,
    LockClosed20Regular,
    SignOut24Regular
} from "@fluentui/react-icons";
import "./style.css";

type Source = { number: number; page: number; chunk?: number; text: string };
type Message = { role: "user" | "assistant"; content: string; sources?: Source[] };
type DocumentState = { document_id: string; filename: string; extension: string; status: string; error?: string; pages?: number };
type ArtifactJob = { job_id: string; kind: "summary" | "translation"; status: string; error?: string };
type Capabilities = { summary: boolean; translation: boolean };
type Session = { document: DocumentState | null; messages?: Message[]; expired?: boolean; last_activity?: string; capabilities?: Capabilities; jobs?: ArtifactJob[] };

const idleTimeoutMinutes = 120;

async function api<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(path, { ...options, credentials: "same-origin", redirect: "error" });
    if (response.status === 401) {
        throw new Error("Dit login er udløbet. Genindlæs siden for at logge ind igen.");
    }
    const content = await response.text();
    let body;
    try {
        body = content ? JSON.parse(content) : null;
    } catch {
        body = null;
    }
    if (!response.ok) {
        if (typeof body?.error === "string") throw new Error(body.error);
        if (response.status === 413) throw new Error("Serveren afviste filens størrelse (HTTP 413).");
        if (response.status === 403) throw new Error("Handlingen blev afvist (HTTP 403). Genindlæs siden og prøv igen.");
        throw new Error(`Forespørgslen mislykkedes (HTTP ${response.status}). Prøv igen.`);
    }
    if (!body || typeof body !== "object" || Array.isArray(body)) {
        throw new Error(`Tjenesten returnerede et tomt eller ugyldigt svar (HTTP ${response.status}). Prøv igen.`);
    }
    return body;
}

function App() {
    const [document, setDocument] = useState<DocumentState | null>(null);
    const [messages, setMessages] = useState<Message[]>([]);
    const [question, setQuestion] = useState("");
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [loading, setLoading] = useState(true);
    const [uploading, setUploading] = useState(false);
    const [asking, setAsking] = useState(false);
    const [ending, setEnding] = useState(false);
    const [dragging, setDragging] = useState(false);
    const [source, setSource] = useState<Source | null>(null);
    const [capabilities, setCapabilities] = useState<Capabilities>({ summary: false, translation: false });
    const [jobs, setJobs] = useState<ArtifactJob[]>([]);
    const [summary, setSummary] = useState<Message | null>(null);
    const [summaryRequested, setSummaryRequested] = useState(false);
    const [translationRequested, setTranslationRequested] = useState(false);
    const [sourceLanguage, setSourceLanguage] = useState("auto");
    const [jobPending, setJobPending] = useState(false);
    const [lastActivity, setLastActivity] = useState("");
    const [minutes, setMinutes] = useState(idleTimeoutMinutes);
    const fileInput = useRef<HTMLInputElement>(null);
    const conversation = useRef<HTMLDivElement>(null);
    const currentId = useRef<string | null>(null);
    const lastTouch = useRef(0);
    const dialog = useRef<HTMLDialogElement>(null);
    const mutationPending = useRef(false);
    const revision = useRef(0);

    function adopt(session: Session) {
        const nextId = session.document?.document_id || null;
        if (currentId.current !== nextId) {
            setMessages(session.messages || []);
            setQuestion("");
            setSource(null);
            setSummary(null);
            setJobs([]);
            dialog.current?.close();
        } else if (session.messages) {
            setMessages(session.messages);
        }
        currentId.current = nextId;
        setDocument(session.document);
        if (session.capabilities) setCapabilities(session.capabilities);
        if (session.jobs) setJobs(session.jobs);
        if (session.last_activity) setLastActivity(session.last_activity);
        if (session.expired) setNotice(`Din session er afsluttet efter ${idleTimeoutMinutes} minutters inaktivitet. Dokumentet bliver slettet.`);
    }

    useEffect(() => {
        let disposed = false;
        async function refresh(initial = false) {
            if (mutationPending.current) return;
            const expectedRevision = revision.current;
            try {
                const session = await api<Session>(`/api/session${initial ? "?history=1" : ""}`);
                if (!disposed && !mutationPending.current && expectedRevision === revision.current) {
                    adopt(session);
                    setLoading(false);
                }
            } catch (failure) {
                if (!disposed) {
                    setError((failure as Error).message);
                    setLoading(false);
                }
            }
        }
        void refresh(true);
        const timer = window.setInterval(() => void refresh(), 4000);
        return () => {
            disposed = true;
            window.clearInterval(timer);
        };
    }, []);

    useEffect(() => {
        const timer = window.setInterval(() => {
            if (lastActivity) setMinutes(Math.max(0, Math.ceil((Date.parse(lastActivity) + idleTimeoutMinutes * 60_000 - Date.now()) / 60_000)));
        }, 1000);
        return () => window.clearInterval(timer);
    }, [lastActivity]);

    useEffect(() => {
        conversation.current?.scrollTo({ top: conversation.current.scrollHeight, behavior: "smooth" });
    }, [messages, asking]);

    async function upload(file?: File) {
        if (!file || uploading || asking || ending) return;
        setError("");
        setNotice("");
        if (!/\.(pdf|docx)$/i.test(file.name)) {
            setError("Vælg en PDF- eller DOCX-fil.");
            return;
        }
        if (file.size > 20 * 1024 * 1024) {
            setError("Dokumentet må højst fylde 20 MB.");
            return;
        }
        setUploading(true);
        mutationPending.current = true;
        revision.current += 1;
        try {
            const body = new FormData();
            body.append("file", file);
            body.append("summary_requested", String(capabilities.summary && summaryRequested));
            body.append("translation_requested", String(capabilities.translation && translationRequested));
            body.append("source_language", sourceLanguage);
            const result = await api<Session>("/api/upload", { method: "POST", body });
            adopt(result);
            setLastActivity(new Date().toISOString());
            setMessages([]);
        } catch (failure) {
            setError((failure as Error).message);
        } finally {
            mutationPending.current = false;
            setUploading(false);
            if (fileInput.current) fileInput.current.value = "";
        }
    }

    async function endSession(newDocument = false) {
        if (ending || uploading) return;
        setEnding(true);
        mutationPending.current = true;
        revision.current += 1;
        setError("");
        try {
            if (document)
                await api("/api/end", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ document_id: document.document_id })
                });
            adopt({ document: null });
            setMessages([]);
            setNotice(newDocument ? "" : "Sessionen er afsluttet. Dit dokument og samtalen bliver slettet.");
            if (newDocument) fileInput.current?.click();
        } catch (failure) {
            setError((failure as Error).message);
        } finally {
            mutationPending.current = false;
            setEnding(false);
        }
    }

    async function touch() {
        if (!currentId.current || Date.now() - lastTouch.current < 60_000) return;
        lastTouch.current = Date.now();
        try {
            await api("/api/activity", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ document_id: currentId.current })
            });
            setLastActivity(new Date().toISOString());
        } catch (failure) {
            setError((failure as Error).message);
        }
    }

    async function logout() {
        if (ending || uploading) return;
        setEnding(true);
        mutationPending.current = true;
        revision.current += 1;
        try {
            await api("/api/logout", { method: "POST" });
            adopt({ document: null });
            window.location.assign("/.auth/logout?post_logout_redirect_uri=/");
        } catch (failure) {
            setError((failure as Error).message);
        } finally {
            mutationPending.current = false;
            setEnding(false);
        }
    }

    async function changeJob(kind: ArtifactJob["kind"], job?: ArtifactJob, action?: "cancel" | "retry") {
        const activeId = currentId.current;
        if (!activeId || jobPending) return;
        setJobPending(true);
        setError("");
        try {
            const base = `/api/document/${activeId}/jobs`;
            const result = await api<ArtifactJob>(job && action ? `${base}/${job.job_id}/${action}` : base, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ kind, source_language: sourceLanguage })
            });
            if (currentId.current === activeId) {
                setJobs(previous => [...previous.filter(item => item.kind !== kind), result]);
                setLastActivity(new Date().toISOString());
                if (kind === "summary" && action === "retry") setSummary(null);
            }
        } catch (failure) {
            if (currentId.current === activeId) setError((failure as Error).message);
        } finally {
            setJobPending(false);
        }
    }

    async function readSummary(job: ArtifactJob) {
        const activeId = currentId.current;
        if (!activeId) return;
        try {
            const result = await api<Message>(`/api/document/${activeId}/jobs/${job.job_id}/summary`);
            if (currentId.current === activeId) {
                setSummary(result);
                setLastActivity(new Date().toISOString());
            }
        } catch (failure) {
            if (currentId.current === activeId) setError((failure as Error).message);
        }
    }

    async function download(job: ArtifactJob) {
        const activeId = currentId.current;
        if (!activeId) return;
        try {
            const response = await fetch(`/api/document/${activeId}/jobs/${job.job_id}/download`, { credentials: "same-origin", redirect: "error" });
            if (!response.ok) throw new Error(`Download mislykkedes (HTTP ${response.status}).`);
            const content = await response.blob();
            if (currentId.current !== activeId) return;
            if (!content.type.includes("wordprocessingml.document")) throw new Error("Tjenesten returnerede ikke en DOCX-fil.");
            const url = URL.createObjectURL(content);
            const anchor = window.document.createElement("a");
            anchor.href = url;
            anchor.download = job.kind === "summary" ? "resume.docx" : "oversaettelse.docx";
            anchor.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
            setLastActivity(new Date().toISOString());
        } catch (failure) {
            if (currentId.current === activeId) setError((failure as Error).message);
        }
    }

    async function ask(event: React.FormEvent) {
        event.preventDefault();
        if (!document || document.status !== "ready" || asking || !question.trim()) return;
        const activeId = document.document_id;
        const text = question.trim();
        setAsking(true);
        setQuestion("");
        setError("");
        setMessages(previous => [...previous, { role: "user", content: text }]);
        try {
            const answer = await api<Message>("/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ document_id: activeId, question: text })
            });
            if (currentId.current === activeId) {
                setMessages(previous => [...previous, answer]);
                setLastActivity(new Date().toISOString());
            }
        } catch (failure) {
            setError((failure as Error).message);
        } finally {
            setAsking(false);
        }
    }

    function showSource(selected: Source) {
        setSource(selected);
        dialog.current?.showModal();
        void touch();
    }
    const ready = document?.status === "ready";
    const busy = uploading || (document && !["ready", "failed"].includes(document.status));
    const status = uploading
        ? "Uploader"
        : ready
          ? "Klar"
          : document?.status === "failed"
            ? "Kunne ikke behandles"
            : document?.status === "checking"
              ? "Klargør søgning"
              : "Behandler dokument";

    return (
        <div className="document-app">
            <header className="brand-header">
                <img src="/documentchat/almbrand.svg" width="160" height="56" alt="Alm. Brand" />
                <span className="demo-label">DEMO</span>
                <div className="header-spacer" />
                <span className="private-label">
                    <LockClosed20Regular /> Privat session
                </span>
                <button
                    className="icon-button"
                    title="Log ud"
                    aria-label="Log ud"
                    disabled={ending || uploading}
                    onClick={() => void logout()}
                >
                    <SignOut24Regular />
                </button>
            </header>
            <main className="workspace">
                <section className="workspace-heading">
                    <div>
                        <p className="eyebrow">DOKUMENTCHAT</p>
                        <h1>Spørg dit dokument</h1>
                    </div>
                    {document && (
                        <span className="expiry">
                            <span className="status-dot" /> {minutes} min. tilbage
                        </span>
                    )}
                </section>
                {(capabilities.summary || capabilities.translation) && (
                    <div className="artifact-options" aria-label="Dokumentfunktioner">
                        {!document ? <>
                            {capabilities.summary && <label><input type="checkbox" checked={summaryRequested} onChange={event => setSummaryRequested(event.target.checked)} /> Resumé på dansk</label>}
                            {capabilities.translation && <label><input type="checkbox" checked={translationRequested} onChange={event => setTranslationRequested(event.target.checked)} /> Oversæt til dansk</label>}
                        </> : <>
                            {capabilities.summary && !jobs.some(job => job.kind === "summary") && <button className="text-button" disabled={!ready || jobPending || ending} onClick={() => void changeJob("summary")}><DocumentText24Regular /> Lav resumé</button>}
                            {capabilities.translation && !jobs.some(job => job.kind === "translation") && <button className="text-button" disabled={!ready || jobPending || ending} onClick={() => void changeJob("translation")}><DocumentText24Regular /> Oversæt til dansk</button>}
                        </>}
                        {capabilities.translation && <label>Kildesprog <select aria-label="Kildesprog" value={sourceLanguage} onChange={event => setSourceLanguage(event.target.value)}><option value="auto">Automatisk</option><option value="da">Dansk</option><option value="en">Engelsk</option><option value="de">Tysk</option></select></label>}
                    </div>
                )}
                <input
                    ref={fileInput}
                    className="file-input"
                    type="file"
                    accept=".pdf,.docx"
                    aria-label="Vælg dokument"
                    onChange={event => void upload(event.target.files?.[0])}
                />
                {loading ? (
                    <div className="loading-state" role="status">
                        <span className="spinner" /> Henter din session
                    </div>
                ) : document ? (
                    <section className="document-strip" aria-label="Aktivt dokument">
                        <DocumentText24Regular className="document-icon" />
                        <div className="document-info">
                            <strong>{document.filename}</strong>
                            <span role="status">
                                {busy ? <span className="spinner small" /> : ready ? <Checkmark24Regular /> : null}
                                {status}
                                {ready && document.extension === ".pdf" && document.pages ? ` · ${document.pages} sider` : ""}
                            </span>
                        </div>
                        <div className="document-actions">
                            <button
                                className="text-button"
                                title="Nyt dokument"
                                aria-label="Nyt dokument"
                                disabled={ending || uploading}
                                onClick={() => void endSession(true)}
                            >
                                <ArrowReset24Regular />
                                <span>Nyt dokument</span>
                            </button>
                            <button
                                className="icon-button"
                                title="Afslut demo og slet dokument"
                                aria-label="Afslut demo og slet dokument"
                                disabled={ending || uploading}
                                onClick={() => void endSession()}
                            >
                                <Dismiss24Regular />
                            </button>
                        </div>
                    </section>
                ) : (
                    <button
                        className={`dropzone ${dragging ? "dragging" : ""}`}
                        disabled={uploading}
                        onClick={() => fileInput.current?.click()}
                        onDragOver={event => {
                            event.preventDefault();
                            setDragging(true);
                        }}
                        onDragLeave={() => setDragging(false)}
                        onDrop={event => {
                            event.preventDefault();
                            setDragging(false);
                            void upload(event.dataTransfer.files[0]);
                        }}
                    >
                        {uploading ? <span className="spinner" /> : <ArrowUpload24Regular />}
                        <strong>{uploading ? "Uploader dokument" : "Drop dit dokument her"}</strong>
                        <span>PDF eller Word · Maks. 20 MB · PDF maks. 100 sider</span>
                    </button>
                )}
                {error && (
                    <div className="feedback error" role="alert">
                        {error}
                        <button className="icon-button" title="Luk besked" aria-label="Luk besked" onClick={() => setError("")}>
                            <Dismiss24Regular />
                        </button>
                    </div>
                )}
                {document?.error && (
                    <div className="feedback error" role="alert">
                        {document.error}
                    </div>
                )}
                {notice && (
                    <div className="feedback" role="status">
                        {notice}
                    </div>
                )}
                <div className="conversation" ref={conversation} aria-label="Samtale" aria-live="polite" aria-busy={asking}>
                    {jobs.map(job => <section className="artifact-status" key={job.job_id} aria-label={job.kind === "summary" ? "Resumé" : "Oversættelse"}>
                        <div className="artifact-heading"><strong>{job.kind === "summary" ? "Resumé" : "Oversættelse"}</strong>
                            <span role="status">{({ queued: "I kø", running: "Behandler hele dokumentet", ready: "Klar", not_needed: "Dokumentet er allerede på dansk", failed: "Kunne ikke behandles", cancelling: "Annullerer", cancelled: "Annulleret", submission_unknown: "Afbrudt – status uafklaret" } as Record<string, string>)[job.status] || job.status}</span>
                        </div>
                        {job.error && <p role="alert">{job.error}</p>}
                        <div className="artifact-actions">
                            {job.status === "ready" && <>
                                {job.kind === "summary" && <button className="text-button" onClick={() => void readSummary(job)}><DocumentText24Regular /> Vis resumé</button>}
                                <button className="text-button" title="Download DOCX" onClick={() => void download(job)}><ArrowDownload24Regular /> Download DOCX</button>
                            </>}
                            {["queued", "running"].includes(job.status) && <button className="text-button" disabled={jobPending || ending} onClick={() => void changeJob(job.kind, job, "cancel")}><Dismiss24Regular /> Annuller</button>}
                            {["failed", "cancelled"].includes(job.status) && <button className="text-button" disabled={jobPending || ending} onClick={() => void changeJob(job.kind, job, "retry")}><ArrowReset24Regular /> Prøv igen</button>}
                        </div>
                    </section>)}
                    {summary && <article className="message assistant" aria-label="Dokumentresumé">
                        <div className="artifact-heading"><h2>Resumé</h2><button className="icon-button" title="Luk resumé" aria-label="Luk resumé" onClick={() => setSummary(null)}><Dismiss24Regular /></button></div>
                        <div className="message-content"><ReactMarkdown components={{ a: ({ href, children }) => {
                            const match = href?.match(/^#source-(\d+)$/);
                            const citation = match ? summary.sources?.find(item => item.number === Number(match[1])) : null;
                            return citation ? <button className="inline-citation" title={`Kilde ${citation.number}`} onClick={() => showSource(citation)}>{children}</button> : <span>{children}</span>;
                        } }}>{summary.content.replace(/\[(\d+)\](?!\()/g, "[$1](#source-$1)")}</ReactMarkdown></div>
                    </article>}
                    {messages.length === 0 && !summary && jobs.length === 0 && (
                        <div className="conversation-empty">
                            <span className="empty-mark">?</span>
                            <h2>{ready ? "Hvad vil du vide?" : busy ? "Dit dokument bliver gjort klar" : "En samtale med dit dokument"}</h2>
                        </div>
                    )}
                    {messages.map((message, position) => (
                        <article className={`message ${message.role}`} key={position}>
                            <div className="message-author">{message.role === "user" ? "DIG" : "DOKUMENTSVAR"}</div>
                            <div className="message-content">
                                <ReactMarkdown
                                    components={{
                                        a: ({ href, children }) => {
                                            const match = href?.match(/^#source-(\d+)$/);
                                            const citation = match ? message.sources?.find(item => item.number === Number(match[1])) : null;
                                            return citation ? (
                                                <button className="inline-citation" title={`Kilde ${citation.number}`} onClick={() => showSource(citation)}>
                                                    {children}
                                                </button>
                                            ) : (
                                                <span>{children}</span>
                                            );
                                        }
                                    }}
                                >
                                    {message.content.replace(/\[(\d+)\](?!\()/g, "[$1](#source-$1)")}
                                </ReactMarkdown>
                            </div>
                            {message.sources && message.sources.length > 0 && (
                                <div className="source-list" aria-label="Kilder">
                                    {message.sources.map(item => (
                                        <button key={item.number} onClick={() => showSource(item)}>
                                            <DocumentText24Regular />
                                            <span>
                                                [{item.number}] {document?.extension === ".pdf" ? `Fra side ${item.page}` : `Tekstudsnit ${(item.chunk ?? item.number - 1) + 1}`}
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </article>
                    ))}
                    {asking && (
                        <div className="answer-loading" role="status">
                            <span className="spinner small" /> Finder svar i dokumentet
                        </div>
                    )}
                </div>
                <form className="composer" onSubmit={event => void ask(event)}>
                    <textarea
                        aria-label="Dit spørgsmål"
                        placeholder="Stil et spørgsmål til dokumentet …"
                        value={question}
                        maxLength={4000}
                        disabled={!ready || asking || ending}
                        rows={2}
                        onChange={event => {
                            setQuestion(event.target.value);
                            void touch();
                        }}
                        onKeyDown={event => {
                            if (event.key === "Enter" && !event.shiftKey) {
                                event.preventDefault();
                                void ask(event);
                            }
                        }}
                    />
                    <button
                        className="send-button"
                        type="submit"
                        aria-label="Send spørgsmål"
                        title="Send spørgsmål"
                        disabled={!ready || asking || ending || !question.trim()}
                    >
                        <ArrowUp24Regular />
                    </button>
                </form>
                <footer className="workspace-footer">
                    <span>Alm. Brand · Demo</span>
                    <span>AI-svar kan indeholde fejl</span>
                </footer>
            </main>
            <dialog
                ref={dialog}
                className="source-dialog"
                onCancel={() => setSource(null)}
                onClick={event => {
                    if (event.target === event.currentTarget) {
                        dialog.current?.close();
                        setSource(null);
                    }
                }}
            >
                <div className="dialog-heading">
                    <h2>Kilde {source?.number}</h2>
                    <button
                        className="icon-button"
                        aria-label="Luk kilde"
                        title="Luk kilde"
                        onClick={() => {
                            dialog.current?.close();
                            setSource(null);
                        }}
                    >
                        <Dismiss24Regular />
                    </button>
                </div>
                <p className="source-location">
                    {document?.filename} · {document?.extension === ".pdf" ? `Fra side ${source?.page}` : `Tekstudsnit ${(source?.chunk || 0) + 1}`}
                </p>
                <div className="source-text">{source?.text}</div>
                {document && (
                    <a className="download-link" href={`/api/document/${document.document_id}/original`} onClick={() => void touch()}>
                        <ArrowDownload24Regular /> Hent originaldokument
                    </a>
                )}
            </dialog>
        </div>
    );
}

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <App />
    </StrictMode>
);
