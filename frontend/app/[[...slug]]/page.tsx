'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Activity, Bot, Boxes, BrainCircuit, CalendarClock, ChevronRight, CircleHelp, Cloud,
  Database, FileCog, Gauge, Home, KeyRound, LayoutDashboard, Menu, Router,
  Server, Settings2, ShieldCheck, Sparkles, TerminalSquare, UsersRound, X, LogOut, Mail, Save, UserRound, Rocket, Leaf, Heart, Camera, LockKeyhole, AtSign, Plus, Copy, Network, HardDrive, Cpu, Check, GitBranch, FileArchive, ScanSearch, CircleAlert, UploadCloud, RefreshCw, Github, Unlink,
} from 'lucide-react';
import { api, setOperatorCsrfToken } from '@/lib/api';

type NavItem = { href: string; label: string; icon: typeof Home; apiPage?: string };
type MetricData = { cpu_percent?: number; memory_percent?: number; disk_percent?: number; api_requests?: number; internet_ping_ms?: number; project_count?: number; security_blocked_users?: number };
type ServiceHealth = { state?: string; healthy?: boolean; detail?: string };
type OverviewHealth = { metrics?: MetricData; services?: Record<string, ServiceHealth>; overall?: 'healthy' | 'attention' | 'degraded' };
type Resource = Record<string, unknown>;
type PagePayload = { title?: string; description?: string; resource_count?: number; resources?: Resource[] };
type OperatorSession = { authenticated?: boolean; csrf_token?: string; expires_in?: number };
type OperatorProfile = { uuid?: string; display_name?: string; email?: string; role?: string; updated_at?: string };
type Account = { id: string; email: string; display_name: string; avatar_icon: string; role: string };
type AccountSession = { authenticated?: boolean; csrf_token?: string; expires_in?: number; account?: Account };

const primary: NavItem[] = [
  { href: '/home', label: 'Home', icon: Home },
  { href: '/agent', label: 'Agent', icon: BrainCircuit },
  { href: '/projects', label: 'Projects', icon: LayoutDashboard, apiPage: 'projects' },
  { href: '/overview', label: 'Overview', icon: Gauge, apiPage: 'overview' },
  { href: '/schedules', label: 'Schedules', icon: CalendarClock, apiPage: 'schedules' },
  { href: '/traefik', label: 'Traefik', icon: FileCog, apiPage: 'traefik' },
  { href: '/docker', label: 'Docker', icon: Boxes, apiPage: 'docker' },
];
const administration: NavItem[] = [
  { href: '/settings', label: 'Settings', icon: Settings2 },
  { href: '/profile', label: 'Profile', icon: UsersRound, apiPage: 'profile' },
  { href: '/sessions', label: 'Sessions', icon: ShieldCheck, apiPage: 'sessions' },
  { href: '/servers', label: 'Remote Servers', icon: Server, apiPage: 'remote-servers' },
  { href: '/users', label: 'Users', icon: UsersRound },
  { href: '/audit-logs', label: 'Audit Logs', icon: Activity, apiPage: 'audit-logs' },
  { href: '/ssh-keys', label: 'SSH Keys', icon: KeyRound, apiPage: 'ssh-keys' },
];
const resources: NavItem[] = [
  { href: '/ai', label: 'AI Providers', icon: Sparkles, apiPage: 'ai' },
  { href: '/tags', label: 'Tags', icon: LayoutDashboard, apiPage: 'tags' },
  { href: '/git', label: 'Git', icon: FileCog, apiPage: 'git' },
  { href: '/registry', label: 'Registry', icon: Boxes, apiPage: 'registry' },
  { href: '/9router', label: '9Router', icon: Router },
  { href: '/dns-providers', label: 'DNS Providers', icon: Cloud, apiPage: 'dns-providers' },
  { href: '/s3-destinations', label: 'S3 Destinations', icon: Cloud, apiPage: 's3-destinations' },
  { href: '/certificates', label: 'Certificates', icon: ShieldCheck, apiPage: 'certificates' },
  { href: '/notifications', label: 'Notifications', icon: Activity, apiPage: 'notifications' },
  { href: '/billing', label: 'Billing', icon: Gauge, apiPage: 'billing' },
  { href: '/license', label: 'License', icon: ShieldCheck, apiPage: 'license' },
  { href: '/sso', label: 'SSO', icon: UsersRound, apiPage: 'sso' },
  { href: '/documentation', label: 'Documentation', icon: FileCog, apiPage: 'documentation' },
  { href: '/support', label: 'Support', icon: CircleHelp, apiPage: 'support' },
];

function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const reload = () => {
    if (!path) return;
    setLoading(true);
    api<T>(path).then(setData).catch((value: Error) => setError(value.message)).finally(() => setLoading(false));
  };
  useEffect(() => { reload(); }, [path]); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, loading, reload };
}

function Navigation({ onNavigate }: { onNavigate: () => void }) {
  const pathname = usePathname();
  const block = (title: string, entries: NavItem[]) => <section className="navGroup" key={title}><p>{title}</p>{entries.map(({ href, label, icon: Icon }) => <Link onClick={onNavigate} key={href} className={pathname === href || (href === '/home' && pathname === '/') ? 'navLink active' : 'navLink'} href={href}><Icon size={18}/><span>{label}</span></Link>)}</section>;
  return <nav className="sideNav"><div className="brand"><span className="brandMark">S</span><strong>Syte</strong></div>{block('Workspace', primary)}{block('Administration', administration)}{block('Resources', resources)}<div className="navFooter"><ShieldCheck size={16}/><span>Self-hosted operator console</span></div></nav>;
}

function HomePage() {
  const metrics = useApi<MetricData>('/platform/overview/metrics');
  const cards = [
    ['CPU', `${Math.round(metrics.data?.cpu_percent || 0)}%`, Activity], ['RAM', `${Math.round(metrics.data?.memory_percent || 0)}%`, Gauge],
    ['Storage', `${Math.round(metrics.data?.disk_percent || 0)}%`, Database], ['Projects', String(metrics.data?.project_count || 0), LayoutDashboard],
    ['API requests', String(metrics.data?.api_requests || 0), Activity], ['Network', metrics.data?.internet_ping_ms ? `${Math.round(metrics.data.internet_ping_ms)} ms` : '—', Cloud],
  ];
  return <><PageHeader eyebrow="Operator home" title="Your platform at a glance" description="The familiar Syte command center, now backed by live FastAPI metrics." action={metrics.reload}/><div className="metricGrid">{cards.map(([label, value, Icon]) => <article className="metric" key={String(label)}><Icon size={19}/><span>{String(label)}</span><strong>{String(value)}</strong></article>)}</div><section className="legacyPanel"><div><p className="eyebrow">Deployment workspace</p><h2>Projects</h2><p>Open a project to deploy, inspect logs, and manage previews using the existing platform APIs.</p></div><Link className="darkButton" href="/projects">Open projects <ChevronRight size={16}/></Link></section></>;
}

type SourceAnalysis = { status?: string; source_type?: string; base_directory?: string; language?: string; framework?: string; build_pack?: string; files_detected?: number; package_manager?: string; install_command?: string; build_command?: string; start_command?: string; exposed_port?: number; warnings?: string[]; error?: string; environment_suggestions?: Array<{ key: string; source?: string }> };
type ImportedProject = { id: string; name: string; status?: string; git_url?: string; domain?: string; running?: boolean };
type ImportPayload = { project: ImportedProject; analysis: SourceAnalysis; message?: string };
type GitHubConnection = { configured: boolean; connected: boolean; login?: string; avatar_url?: string };
type GitHubRepository = { full_name: string; name: string; clone_url: string; default_branch: string; private: boolean; description?: string };
type GitHubBranch = { name: string; sha?: string };

function readEnvironment(text: string): Record<string, string> {
  return text.split(/\r?\n/).reduce<Record<string, string>>((env, line) => { const index = line.indexOf('='); if (index > 0) { const key = line.slice(0, index).trim(); if (/^[A-Z][A-Z0-9_]{1,127}$/.test(key)) env[key] = line.slice(index + 1); } return env; }, {});
}

function ProjectsPage() {
  const projects = useApi<ImportedProject[]>('/projects');
  const [source, setSource] = useState<'git' | 'zip'>('git');
  const [name, setName] = useState(''); const [repository, setRepository] = useState(''); const [branch, setBranch] = useState('main'); const [baseDirectory, setBaseDirectory] = useState('/');
  const [archive, setArchive] = useState<File | null>(null); const [draft, setDraft] = useState<ImportedProject | null>(null); const [analysis, setAnalysis] = useState<SourceAnalysis | null>(null);
  const [environment, setEnvironment] = useState(''); const [startCommand, setStartCommand] = useState(''); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState<string | null>(null);
  const [github, setGithub] = useState<GitHubConnection | null>(null); const [githubRepositories, setGithubRepositories] = useState<GitHubRepository[]>([]); const [githubSearch, setGithubSearch] = useState(''); const [githubSelected, setGithubSelected] = useState<GitHubRepository | null>(null); const [githubBranches, setGithubBranches] = useState<GitHubBranch[]>([]); const [githubBusy, setGithubBusy] = useState(false);
  const loadGitHubRepositories = async () => { const result = await api<{ repositories: GitHubRepository[] }>('/projects/git/github/repositories'); setGithubRepositories(result.repositories || []); };
  const loadGitHubStatus = async (withRepositories = true) => { try { const result = await api<GitHubConnection>('/projects/git/github/status'); setGithub(result); if (result.connected && withRepositories) await loadGitHubRepositories(); } catch (error) { setGithub({ configured: false, connected: false }); } };
  useEffect(() => { void loadGitHubStatus(); const onMessage = (event: MessageEvent) => { if (event.origin === window.location.origin && event.data?.type === 'syte-github-oauth') { if (event.data.ok) { setNotice(`GitHub connected${event.data.login ? ` as ${event.data.login}` : ''}.`); void loadGitHubStatus(); } else if (event.data.message) setNotice(event.data.message); } }; window.addEventListener('message', onMessage); return () => window.removeEventListener('message', onMessage); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const connectGitHub = async () => { const popup = window.open('', 'syte-github-connect', 'popup=yes,width=600,height=720'); if (!popup) { setNotice('Allow pop-ups for this site to connect GitHub.'); return; } try { const result = await api<{ authorization_url: string }>('/projects/git/github/connect'); popup.location.href = result.authorization_url; } catch (error) { popup.close(); setNotice((error as Error).message); } };
  const disconnectGitHub = async () => { setGithubBusy(true); try { await api('/projects/git/github/disconnect', { method: 'DELETE' }); setGithub((current) => ({ configured: Boolean(current?.configured), connected: false })); setGithubRepositories([]); setGithubSelected(null); setGithubBranches([]); setNotice('GitHub connection removed.'); } catch (error) { setNotice((error as Error).message); } finally { setGithubBusy(false); } };
  const selectGitHubRepository = async (selected: GitHubRepository) => { setGithubSelected(selected); setRepository(selected.clone_url); setBranch(selected.default_branch || 'main'); if (!name.trim()) setName(selected.name); setGithubBusy(true); try { const result = await api<{ branches: GitHubBranch[] }>(`/projects/git/github/repositories/${encodeURIComponent(selected.full_name)}/branches`); const branches = result.branches || []; setGithubBranches(branches); if (branches.some((item) => item.name === selected.default_branch)) setBranch(selected.default_branch); else if (branches[0]?.name) setBranch(branches[0].name); } catch (error) { setNotice((error as Error).message); } finally { setGithubBusy(false); } };
  const addSuggestion = (key: string) => setEnvironment((current) => new RegExp(`(^|\\n)${key.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&')}=`).test(current) ? current : `${current.trimEnd()}${current.trim() ? '\n' : ''}${key}=`);
  const importSource = async () => { if (!name.trim()) throw new Error('Enter a project name.'); if (source === 'git') { if (githubSelected) return api<ImportPayload>('/projects/import/github', { method: 'POST', body: JSON.stringify({ name: name.trim(), repository: githubSelected.full_name, branch: branch.trim() || 'main', base_directory: baseDirectory.trim() || '/' }) }); if (!repository.trim()) throw new Error('Enter a repository URL or choose a connected GitHub repository.'); return api<ImportPayload>('/projects/import/repository', { method: 'POST', body: JSON.stringify({ name: name.trim(), git_url: repository.trim(), branch: branch.trim() || 'main', base_directory: baseDirectory.trim() || '/' }) }); } if (!archive) throw new Error('Choose a ZIP archive.'); const form = new FormData(); form.set('name', name.trim()); form.set('base_directory', baseDirectory.trim() || '/'); form.set('archive', archive); return api<ImportPayload>('/projects/import/zip', { method: 'POST', body: form }); };
  const analyze = async () => { setBusy(true); setNotice(null); try { if (!draft) { const result = await importSource(); setDraft(result.project); setAnalysis(result.analysis); setStartCommand(result.analysis.start_command || ''); setNotice(result.message || 'Source imported and analyzed.'); } else { const result = await api<{ analysis: SourceAnalysis }>(`/projects/${draft.id}/analyze`, { method: 'POST', body: JSON.stringify({ base_directory: baseDirectory.trim() || '/' }) }); setAnalysis(result.analysis); setStartCommand(result.analysis.start_command || ''); setNotice('Build plan refreshed.'); } } catch (error) { setNotice((error as Error).message); } finally { setBusy(false); } };
  const deploy = async () => { if (!draft || !analysis || analysis.status !== 'ready') return; setBusy(true); setNotice(null); try { const result = await api<ImportPayload>(`/projects/${draft.id}/deploy-detected`, { method: 'POST', body: JSON.stringify({ base_directory: baseDirectory.trim() || '/', env_vars: readEnvironment(environment), start_command: startCommand || null }) }); setNotice(result.message || 'Deployment queued.'); projects.reload(); } catch (error) { setNotice((error as Error).message); } finally { setBusy(false); } };
  const reset = () => { setDraft(null); setAnalysis(null); setEnvironment(''); setStartCommand(''); setNotice(null); setArchive(null); };
  const values = analysis ? [['Framework', analysis.framework || 'Custom'], ['Language', analysis.language || 'Unknown'], ['Build pack', analysis.build_pack || 'Manual'], ['Port', analysis.exposed_port ? `:${analysis.exposed_port}` : 'Auto'], ['Files', String(analysis.files_detected || 0)], ['Package manager', analysis.package_manager || 'Auto']] : [];
  const filteredGitHubRepositories = githubRepositories.filter((item) => !githubSearch.trim() || [item.full_name, item.description || ''].join(' ').toLowerCase().includes(githubSearch.trim().toLowerCase()));
  return <><PageHeader eyebrow="Deployments" title="Import and deploy a project" description="Bring a public Git repository or ZIP archive. Review the detected framework and environment keys before starting a production deployment." action={projects.reload}/><section className="projectDeployLayout"><form className="projectImportPanel" onSubmit={(event) => { event.preventDefault(); if (draft) deploy(); else analyze(); }}><div className="projectDeployIntro"><span className="projectStep">1</span><div><p className="eyebrow">Source</p><h2>Import your code</h2></div>{draft && <button type="button" onClick={reset} className="lightButton">New draft</button>}</div><label className="projectLabel">Project name<input value={name} disabled={Boolean(draft)} onChange={(event) => setName(event.target.value)} placeholder="my-app" autoComplete="off"/></label><div className="sourceToggle" role="tablist" aria-label="Project source"><button type="button" className={source === 'git' ? 'active' : ''} onClick={() => !draft && setSource('git')} aria-selected={source === 'git'}><GitBranch size={16}/>Git repository</button><button type="button" className={source === 'zip' ? 'active' : ''} onClick={() => !draft && setSource('zip')} aria-selected={source === 'zip'}><FileArchive size={16}/>Upload ZIP</button></div>{source === 'git' ? <><section className="githubSourceCard"><div className="githubSourceHead"><span className="githubSourceMark"><Github size={18}/></span><div><strong>GitHub Connected Source</strong><p>{github?.connected ? 'Choose a repository and branch. Credentials are encrypted and only used server-side while cloning.' : github?.configured ? 'Connect GitHub to browse every repository available to your account, including private repositories.' : 'GitHub OAuth must be configured by an operator before a source account can connect.'}</p></div></div>{github?.connected ? <div className="githubAccount"><span className="githubPresence"/>{github.avatar_url ? <img src={github.avatar_url} alt=""/> : <Github size={16}/>}<strong>{github.login || 'Connected GitHub account'}</strong><button type="button" className="lightButton" onClick={disconnectGitHub} disabled={githubBusy || Boolean(draft)}><Unlink size={14}/>Disconnect</button></div> : <button type="button" className="lightButton githubConnectButton" onClick={connectGitHub} disabled={!github?.configured || githubBusy || Boolean(draft)}><Github size={15}/>Connect GitHub</button>}</section>{github?.connected && <section className="githubRepositoryPicker"><div className="githubPickerTop"><div><p className="eyebrow">Connected repositories</p><h3>Choose a GitHub repository</h3></div><button type="button" className="iconButton" aria-label="Refresh repositories" onClick={() => void loadGitHubRepositories()} disabled={githubBusy || Boolean(draft)}><RefreshCw size={15}/></button></div><label className="projectLabel">Search repositories<input value={githubSearch} disabled={Boolean(draft)} onChange={(event) => setGithubSearch(event.target.value)} placeholder="Owner, repository, or description"/></label><div className="githubRepositoryList">{filteredGitHubRepositories.length ? filteredGitHubRepositories.map((item) => <button type="button" onClick={() => void selectGitHubRepository(item)} disabled={Boolean(draft)} className={githubSelected?.full_name === item.full_name ? 'selected' : ''} key={item.full_name}><span><strong>{item.full_name}</strong>{item.description && <small>{item.description}</small>}</span><em>{item.private ? 'Private' : 'Public'}</em></button>) : <p>No repositories match this connection or search.</p>}</div><div className="projectInlineFields"><label className="projectLabel">Branch<select value={branch} disabled={!githubSelected || githubBusy || Boolean(draft)} onChange={(event) => setBranch(event.target.value)}>{githubBranches.length ? githubBranches.map((item) => <option key={item.name} value={item.name}>{item.name}</option>) : <option value="">Choose a repository first</option>}</select></label><label className="projectLabel">Base directory<input value={baseDirectory} onChange={(event) => setBaseDirectory(event.target.value)} placeholder="/"/></label></div></section>}<div className="githubManualRule"><span>or use a public URL</span></div><label className="projectLabel">Repository URL<input value={repository} disabled={Boolean(draft)} onChange={(event) => { setRepository(event.target.value); if (githubSelected && event.target.value !== githubSelected.clone_url) { setGithubSelected(null); setGithubBranches([]); } }} placeholder="https://github.com/you/project.git" inputMode="url"/></label><div className="projectInlineFields"><label className="projectLabel">Branch<input value={branch} disabled={Boolean(draft) || Boolean(githubSelected)} onChange={(event) => setBranch(event.target.value)} placeholder="main"/></label>{!github?.connected && <label className="projectLabel">Base directory<input value={baseDirectory} onChange={(event) => setBaseDirectory(event.target.value)} placeholder="/"/></label>}</div></> : <><label className="archiveField"><input type="file" accept=".zip,application/zip" disabled={Boolean(draft)} onChange={(event) => setArchive(event.target.files?.[0] || null)}/><UploadCloud size={25}/><strong>{archive ? archive.name : 'Choose a ZIP archive'}</strong><span>ZIP only, up to 75 MB. It is inspected before deployment.</span></label><label className="projectLabel">Base directory<input value={baseDirectory} onChange={(event) => setBaseDirectory(event.target.value)} placeholder="/ or apps/web"/></label></>}<p className="projectSafety"><ShieldCheck size={15}/>Source analysis reads manifests and variable names; it never imports environment values.</p>{notice && <p className="projectNotice">{notice}</p>}<button className="darkButton projectPrimary" disabled={busy || Boolean(draft && analysis?.status !== 'ready')} type="submit">{busy ? <><RefreshCw size={16}/>Working…</> : draft ? <><Rocket size={16}/>Deploy project</> : <><ScanSearch size={16}/>Analyze source</>}</button></form><aside className="projectAnalysisPanel" aria-live="polite"><div className="projectDeployIntro"><span className="projectStep muted">2</span><div><p className="eyebrow">Configuration</p><h2>{analysis ? 'Review build plan' : 'Automatic detection'}</h2></div>{draft && <button type="button" onClick={analyze} className="iconButton" aria-label="Analyze again"><RefreshCw size={16}/></button>}</div>{analysis ? <><div className="detectedConfigGrid">{values.map(([label, value]) => <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</div>{(analysis.warnings?.length || analysis.error) ? <div className="projectWarnings"><CircleAlert size={17}/><p>{[...(analysis.warnings || []), ...(analysis.error ? [analysis.error] : [])].join(' ')}</p></div> : null}<section className="environmentReview"><div><h3>Suggested variables</h3><p>Click a key to add an empty value.</p></div><div className="environmentChips">{analysis.environment_suggestions?.length ? analysis.environment_suggestions.map((item) => <button type="button" onClick={() => addSuggestion(item.key)} key={item.key}><strong>{item.key}</strong><small>{item.source}</small><Plus size={13}/></button>) : <span>No source references found.</span>}</div></section><label className="projectLabel">Environment values<textarea rows={5} value={environment} onChange={(event) => setEnvironment(event.target.value)} placeholder={'DATABASE_URL=\nAPI_KEY='}/></label><details className="projectBuildDetails"><summary>Build settings <ChevronRight size={15}/></summary><label className="projectLabel">Start command<input value={startCommand} onChange={(event) => setStartCommand(event.target.value)} placeholder="Detected automatically"/></label><p>Install and build commands are inferred from the repository manifest and package manager.</p></details></> : <div className="analysisEmpty"><ScanSearch size={28}/><h3>Framework detection waits here</h3><p>After import, Syte identifies Node.js, Python, Go, Rust, PHP, Ruby, Java, .NET, Bun, Deno, static sites, Dockerfiles, and common web frameworks.</p></div>}</aside></section><section className="projectRecent"><div><p className="eyebrow">Projects</p><h2>Recent deployments</h2></div><button onClick={projects.reload} className="lightButton">Refresh</button><div>{projects.data?.length ? projects.data.slice(0, 6).map((project) => <article key={project.id}><span><strong>{project.name}</strong><small>{project.domain || project.git_url || 'Local source'}</small></span><em className={project.running ? 'running' : ''}>{project.running ? 'running' : project.status || 'created'}</em></article>) : <p className="empty">No projects yet. Import a source above to create the first deployment.</p>}</div></section></>;
}

function HealthGauge({ label, value }: { label: string; value?: number }) {
  const numeric = Math.max(0, Math.min(100, Math.round(value || 0)));
  const tone = numeric >= 85 ? 'danger' : numeric >= 70 ? 'warning' : 'healthy';
  return <div className={`healthGauge ${tone}`} role="img" aria-label={`${label} ${numeric} percent`}><svg viewBox="0 0 120 70" aria-hidden="true"><path className="healthTrack" pathLength="100" d="M15 60 A45 45 0 0 1 105 60"/><path className="healthValue" pathLength="100" strokeDasharray={`${numeric} 100`} d="M15 60 A45 45 0 0 1 105 60"/></svg><strong>{numeric}%</strong><span>{label}</span></div>;
}

function HealthNode({ label, service }: { label: string; service?: ServiceHealth }) {
  const state = service?.state || 'unavailable';
  return <div className={`healthNode ${state}`} title={service?.detail || 'Status unavailable'}><i>{state === 'healthy' ? '✓' : state === 'attention' || state === 'warning' ? '!' : '×'}</i><strong>{label}</strong><small>{state}</small></div>;
}

function OverviewPage() {
  const health = useApi<OverviewHealth>('/platform/overview/health');
  const metrics = health.data?.metrics || {};
  const services = health.data?.services || {};
  const overall = health.data?.overall || 'attention';
  const label = overall === 'healthy' ? 'everything up' : overall === 'attention' ? 'attention needed' : 'service degraded';
  return <section className="healthOverview" aria-live="polite"><div className="healthTop"><span>Syte</span><button onClick={health.reload} className="iconButton" aria-label="Refresh Overview"><Activity size={17}/></button></div>{health.loading ? <p className="healthLoading">Loading system health…</p> : <><div className="healthGaugePanel"><HealthGauge label="CPU" value={metrics.cpu_percent}/><HealthGauge label="RAM" value={metrics.memory_percent}/><HealthGauge label="DISK" value={metrics.disk_percent}/></div><div className={`healthStatus ${overall}`}>{label}</div><div className="healthTopology"><HealthNode label="Web service" service={services.web}/><div className="healthBranches" aria-hidden="true"><span/><span/><span/></div><div className="healthChildren"><HealthNode label="API" service={services.api}/><HealthNode label="Apps" service={services.apps}/><HealthNode label="9Router" service={services.router}/></div></div></>}</section>;
}

function AgentPage() {
  const status = useApi<Record<string, unknown>>('/syra/status');
  return <><PageHeader eyebrow="AI workspace" title="Syte Agent" description="Restored as a first-class navigation destination for model, session, and agent workflows." action={status.reload}/><section className="agentCanvas"><div className="agentOrb"><Sparkles size={28}/></div><div><span className="statusPill">{status.loading ? 'Checking runtime' : status.error ? 'Runtime unavailable' : 'Agent runtime ready'}</span><h2>Plan, build, deploy.</h2><p>Use the Agent area to continue the original Syte AI workflows. FastAPI session and model endpoints remain the source of truth.</p><div className="agentActions"><button className="darkButton">Start a task <ChevronRight size={16}/></button><Link className="lightButton" href="/settings">Agent settings</Link></div></div></section><section className="twoColumn"><InfoCard icon={TerminalSquare} title="Active sessions" body="Agent sessions are served by the existing project and Turso session APIs."/><InfoCard icon={Bot} title="Model providers" body="Provider and model status continues to be managed by the FastAPI runtime."/></section></>;
}

function RouterPage() {
  const status = useApi<Record<string, unknown>>('/router/status');
  const online = !status.error && !status.loading;
  return <><PageHeader eyebrow="Runtime routing" title="9Router" description="Restored to the primary menu for local model routing, process status, and operational controls." action={status.reload}/><section className="routerHero"><div><span className={online ? 'statusPill online' : 'statusPill'}>{status.loading ? 'Checking 9Router' : online ? 'Router status available' : 'Router needs attention'}</span><h2>Model gateway & routing layer</h2><p>Use the established 9Router service controls to inspect runtime availability and recover the local routing stack.</p></div><Router size={76}/></section><section className="routerCards"><InfoCard icon={Server} title="Service status" body={online ? 'The 9Router status endpoint responded successfully.' : (status.error || 'Checking service state.')}/><InfoCard icon={FileCog} title="Proxy configuration" body="Inspect current routing and reverse-proxy configuration from the original control surface."/><InfoCard icon={Activity} title="Runtime logs" body="Open the existing log panel to investigate model gateway events."/></section></>;
}

function SettingsPage() {
  return <><PageHeader eyebrow="Instance configuration" title="Settings" description="The classic Syte settings entry point for instance, Git, advanced controls, and operator preferences."/><section className="settingsLayout"><article><Settings2 size={22}/><h2>General</h2><p>Instance configuration, domains, preview behavior, and default operational preferences.</p><button className="lightButton">Open general settings</button></article><article><FileCog size={22}/><h2>Git</h2><p>Repository branch defaults, project integration, and pull request workflows.</p><button className="lightButton">Open Git settings</button></article><article><ShieldCheck size={22}/><h2>Advanced</h2><p>Provider configuration, feature flags, lifecycle controls, and diagnostics.</p><button className="lightButton">Open advanced settings</button></article></section></>;
}

function UsersPage() {
  const tokens = useApi<Array<{ id?: string; name?: string; created_at?: string }>>('/tokens');
  const [name, setName] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const create = async () => { if (!name.trim()) return; try { const result = await api<{ token?: string }>('/tokens', { method: 'POST', body: JSON.stringify({ name }) }); setMessage(result.token ? 'Token created. Copy it now; it will not be shown again.' : 'Token created.'); setName(''); tokens.reload(); } catch (error) { setMessage((error as Error).message); } };
  return <><PageHeader eyebrow="Access management" title="Users & API tokens" description="Preserves the original FastAPI-backed access-token workflow in the Next.js operator UI." action={tokens.reload}/><section className="legacyPanel compact"><div><p className="eyebrow">Create automation access</p><h2>New API token</h2><p>Issue a token for CI, deployments, or external integrations.</p></div><div className="tokenCreate"><input value={name} onChange={(event) => setName(event.target.value)} placeholder="ci-deploy"/><button onClick={create} className="darkButton">Create token</button></div></section>{message && <p className="notice">{message}</p>}<section className="resourceList">{tokens.data?.length ? tokens.data.map((token, index) => <article key={token.id || index}><span><strong>{token.name || 'Operator token'}</strong><small>{token.created_at || 'Created recently'}</small></span><ShieldCheck size={16}/></article>) : <p className="empty">No tokens returned. Operator authentication is required to manage tokens.</p>}</section></>;
}

function ProfilePage() {
  const [session, setSession] = useState<OperatorSession | null>(null);
  const [profile, setProfile] = useState<OperatorProfile | null>(null);
  const [bootstrapToken, setBootstrapToken] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  const loadProfile = async () => {
    const result = await api<{ profile: OperatorProfile }>('/platform/operator/profile');
    setProfile(result.profile);
    setDisplayName(result.profile.display_name || '');
    setEmail(result.profile.email || '');
  };
  const refreshSession = async () => {
    setBusy(true);
    try {
      const current = await api<OperatorSession>('/operator/session');
      setSession(current);
      setOperatorCsrfToken(current.csrf_token || null);
      if (current.authenticated) await loadProfile();
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  useEffect(() => { refreshSession(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const login = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setMessage(null);
    try {
      const result = await api<OperatorSession & { message?: string }>('/operator/session', { method: 'POST', body: JSON.stringify({ bootstrap_token: bootstrapToken }) });
      setOperatorCsrfToken(result.csrf_token || null); setSession({ authenticated: true, csrf_token: result.csrf_token, expires_in: result.expires_in }); setBootstrapToken(''); await loadProfile();
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setMessage(null);
    try {
      const result = await api<{ profile: OperatorProfile; message: string }>('/platform/operator/profile', { method: 'PUT', body: JSON.stringify({ display_name: displayName, email }) });
      setProfile(result.profile); setMessage(result.message);
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  const logout = async () => {
    setBusy(true);
    try { await api('/operator/session', { method: 'DELETE' }); setOperatorCsrfToken(null); setSession({ authenticated: false }); setProfile(null); setMessage(null); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  if (busy && session === null) return <section className="profileGate"><p className="profileLoading">Checking operator session…</p></section>;
  if (!session?.authenticated) return <section className="profileGate"><div className="shadcnCard loginCard"><div className="cardBrand"><span className="cardMark">S</span><div><p>Syte operator</p><h1>Welcome back</h1></div></div><p className="cardLead">Sign in to manage your operator profile and protected platform controls.</p><form onSubmit={login} className="shadcnForm"><label htmlFor="operator-key">Operator key</label><input id="operator-key" type="password" autoComplete="current-password" value={bootstrapToken} onChange={(event) => setBootstrapToken(event.target.value)} placeholder="Enter your operator key" required/><button disabled={busy} className="shadcnPrimary" type="submit">{busy ? 'Signing in…' : 'Sign in'}</button></form>{message && <p className="formMessage error">{message}</p>}<p className="cardFineprint">Your key is exchanged for an HttpOnly session cookie and is never stored in the browser.</p></div></section>;
  return <section className="profilePage"><header className="profileHeader"><div className="avatarCircle">{(profile?.display_name || 'O').slice(0, 1).toUpperCase()}</div><div><p className="eyebrow">Authenticated operator</p><h1>{profile?.display_name || 'Operator'}</h1><p>{profile?.email || 'No email address configured'}</p></div><button className="shadcnOutline" onClick={logout} disabled={busy}><LogOut size={16}/>Sign out</button></header><div className="profileGrid"><section className="shadcnCard profileDetails"><div className="sectionTitle"><UserRound size={19}/><div><h2>Personal details</h2><p>These details identify this operator in the workspace.</p></div></div><form onSubmit={save} className="shadcnForm"><label htmlFor="profile-name">Display name</label><input id="profile-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Operator name" maxLength={120}/><label htmlFor="profile-email">Email address</label><div className="inputWithIcon"><Mail size={16}/><input id="profile-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="operator@example.com" maxLength={254}/></div><button disabled={busy} className="shadcnPrimary" type="submit"><Save size={16}/>{busy ? 'Saving…' : 'Save changes'}</button></form>{message && <p className="formMessage">{message}</p>}</section><aside className="shadcnCard profileSecurity"><ShieldCheck size={21}/><h2>Session protected</h2><p>This profile uses the active operator session. Protected changes require a same-origin CSRF token.</p><dl><div><dt>Role</dt><dd>{profile?.role || 'operator'}</dd></div><div><dt>Session</dt><dd>{session.expires_in ? `${Math.ceil(session.expires_in / 60)} min remaining` : 'Active'}</dd></div></dl></aside></div></section>;
}

const avatarIcons: Record<string, typeof UserRound> = { user: UserRound, sparkles: Sparkles, shield: ShieldCheck, rocket: Rocket, leaf: Leaf, heart: Heart, camera: Camera };

function AccountAvatar({ account, compact = false }: { account: Account; compact?: boolean }) {
  const Icon = avatarIcons[account.avatar_icon] || UserRound;
  return <span className={compact ? 'accountAvatar compact' : 'accountAvatar'} title={account.display_name}><Icon size={compact ? 17 : 23}/></span>;
}

function LoginScreen({ onAuthenticated }: { onAuthenticated: (session: AccountSession) => void }) {
  const [setup, setSetup] = useState(false);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<{ needs_first_account: boolean }>('/auth/setup').then((data) => { setNeedsSetup(data.needs_first_account); setSetup(data.needs_first_account); }).catch((error: Error) => setMessage(error.message)); }, []);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setMessage(null);
    try {
      const path = setup ? '/auth/setup' : '/auth/login';
      const payload = setup ? { email, password, display_name: displayName } : { email, password };
      const result = await api<AccountSession>(path, { method: 'POST', body: JSON.stringify(payload) });
      if (!result.account || !result.csrf_token) throw new Error('The account session could not be established.');
      setOperatorCsrfToken(result.csrf_token); onAuthenticated({ ...result, authenticated: true });
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  return <main className="accountLogin"><section className="accountLoginBrand"><div className="loginLogo"><Sparkles size={28}/></div><p>Syte</p><h1>Run everything<br/>with confidence.</h1><span>Self-hosted applications, workflows, and infrastructure in one protected workspace.</span></section><section className="accountLoginPanel"><div className="authCard"><div className="authIcon"><LockKeyhole size={20}/></div><p className="eyebrow">{setup ? 'First workspace account' : 'Secure workspace access'}</p><h2>{setup ? 'Create your owner account' : 'Sign in to Syte'}</h2><p className="authLead">{setup ? 'Set the email and password used to administer this Syte instance.' : 'Use your email and password to continue to your workspace.'}</p><form onSubmit={submit} className="shadcnForm authForm">{setup && <><label htmlFor="account-name">Display name</label><input id="account-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Your name" maxLength={120}/></>}<label htmlFor="account-email">Email address</label><div className="inputWithIcon"><AtSign size={16}/><input id="account-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" required/></div><label htmlFor="account-password">Password</label><div className="inputWithIcon"><LockKeyhole size={16}/><input id="account-password" type="password" autoComplete={setup ? 'new-password' : 'current-password'} value={password} onChange={(event) => setPassword(event.target.value)} placeholder={setup ? 'At least 12 characters' : 'Your password'} minLength={setup ? 12 : 1} required/></div><button disabled={busy || needsSetup === null} className="shadcnPrimary authSubmit" type="submit">{busy ? 'Please wait…' : setup ? 'Create account' : 'Sign in'}</button></form>{message && <p className="formMessage error">{message}</p>}{needsSetup === false && <button className="authSwitch" type="button" onClick={() => { setSetup((value) => !value); setMessage(null); }}>{setup ? 'Already have an account? Sign in' : 'Need to set up this instance?'}</button>}<p className="cardFineprint">Email and password sessions use a secure HttpOnly cookie. Your password is never stored in the browser.</p></div></section></main>;
}

function AccountProfilePage({ account, onAccountChange, onSignOut }: { account: Account; onAccountChange: (account: Account) => void; onSignOut: () => void }) {
  const [displayName, setDisplayName] = useState(account.display_name);
  const [avatarIcon, setAvatarIcon] = useState(account.avatar_icon);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (event: React.FormEvent) => { event.preventDefault(); setBusy(true); setMessage(null); try { const result = await api<{ account: Account; message: string }>('/auth/profile', { method: 'PUT', body: JSON.stringify({ display_name: displayName, avatar_icon: avatarIcon }) }); onAccountChange(result.account); setMessage(result.message); } catch (error) { setMessage((error as Error).message); } finally { setBusy(false); } };
  return <section className="accountProfilePage"><header className="profileHeader"><AccountAvatar account={{ ...account, avatar_icon: avatarIcon }}/><div><p className="eyebrow">Your Syte account</p><h1>{displayName || account.display_name}</h1><p>{account.email}</p></div><button className="shadcnOutline" onClick={onSignOut} disabled={busy}><LogOut size={16}/>Sign out</button></header><div className="profileGrid"><section className="shadcnCard profileDetails"><div className="sectionTitle"><UserRound size={19}/><div><h2>Profile</h2><p>Choose the identity shown across your Syte workspace.</p></div></div><form onSubmit={save} className="shadcnForm"><label htmlFor="account-display-name">Display name</label><input id="account-display-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={120} placeholder="Your name"/><label>Profile icon</label><div className="avatarPicker">{Object.entries(avatarIcons).map(([key, Icon]) => <button aria-label={`Use ${key} profile icon`} type="button" key={key} className={avatarIcon === key ? 'avatarChoice selected' : 'avatarChoice'} onClick={() => setAvatarIcon(key)}><Icon size={18}/></button>)}</div><button disabled={busy} className="shadcnPrimary" type="submit"><Save size={16}/>{busy ? 'Saving…' : 'Save profile'}</button></form>{message && <p className="formMessage">{message}</p>}</section><aside className="shadcnCard profileSecurity"><ShieldCheck size={21}/><h2>Account security</h2><p>Your email is your sign-in identity. The profile icon is displayed on every authenticated Syte page.</p><dl><div><dt>Email</dt><dd>{account.email}</dd></div><div><dt>Role</dt><dd>{account.role}</dd></div></dl></aside></div></section>;
}

function BlankPage() {
  return <section className="intentionalBlank" aria-label="Blank workspace"/>;
}

type FleetNode = {
  uuid: string; name: string; host: string; server_type: string; status: string; last_seen_at?: string;
  role_websites: boolean; role_router: boolean; role_workers: boolean; load_balancing_enabled: boolean;
  load_balancing_weight: number; load_percent: number | null; availability_percent: number | null;
  metrics?: { recorded_at?: string } | null;
};
type FleetPayload = {
  nodes: FleetNode[];
  summary: { total_nodes: number; online_nodes: number; website_nodes: number; router_nodes: number; worker_nodes: number };
  load_balancer: { enabled: boolean; strategy: 'least-load' | 'round-robin'; router_server_uuid: string; health_check_path: string; active_router_count: number; eligible_targets: Array<{ uuid: string; name: string; load_percent: number | null; weight: number }> };
};

function RemoteServersPage() {
  const fleet = useApi<FleetPayload>('/platform/fleet');
  const [showEnroll, setShowEnroll] = useState(false);
  const [name, setName] = useState('');
  const [host, setHost] = useState('');
  const [serverType, setServerType] = useState('micro');
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [script, setScript] = useState<{ name: string; code: string } | null>(null);
  const data = fleet.data;

  useEffect(() => {
    if (!script) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setScript(null); };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [script]);

  const updateRoles = async (node: FleetNode, patch: Record<string, boolean | number>) => {
    setBusy(true); setMessage(null);
    try { await api(`/platform/fleet/servers/${node.uuid}/roles`, { method: 'PUT', body: JSON.stringify(patch) }); await fleet.reload(); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  const updateBalancer = async (patch: Record<string, string | boolean>) => {
    if (!data) return;
    setBusy(true); setMessage(null);
    try { await api('/platform/fleet/load-balancer', { method: 'PUT', body: JSON.stringify({ ...data.load_balancer, ...patch }) }); await fleet.reload(); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  const enroll = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !host.trim()) return;
    setBusy(true); setMessage(null);
    try {
      await api('/platform/fleet/servers', { method: 'POST', body: JSON.stringify({ name, host, server_type: serverType, role_websites: true, role_router: serverType === 'edge', role_workers: serverType === 'build', load_balancing_enabled: serverType !== 'build' }) });
      setName(''); setHost(''); setShowEnroll(false); await fleet.reload();
      setMessage('Node enrolled. Open its helper script to begin secure load reporting.');
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  const showScript = async (node: FleetNode) => {
    setBusy(true); setMessage(null);
    try { const result = await api<{ filename: string; script: string }>(`/platform/fleet/servers/${node.uuid}/setup-script`); setScript({ name: result.filename, code: result.script }); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  const copyScript = async () => {
    if (!script) return;
    try { await navigator.clipboard.writeText(script.code); setMessage('Helper script copied. Review it before running it as root.'); }
    catch { setMessage('Copy is unavailable in this browser. Select the script and copy it manually.'); }
  };
  const balanceEnabled = Boolean(data?.load_balancer.enabled);
  const healthyTargets = data?.load_balancer.eligible_targets.length || 0;
  const balancerLabel = balanceEnabled ? `${healthyTargets} healthy target${healthyTargets === 1 ? '' : 's'} ready for traffic` : 'Traffic routing is paused';

  return <section className="fleetPage">
    <PageHeader eyebrow="Infrastructure fleet" title="Remote Servers" description="Add servers, choose their responsibilities, and safely route website traffic using reported node load." action={fleet.reload}/>
    {message && <p className="notice fleetNotice" role="status">{message}</p>}

    <section className="shadcnCard fleetControlCard" aria-label="Load balancer settings">
      <div className="fleetControlIntro"><div className="fleetIcon"><Network size={20}/></div><div><p className="eyebrow">Traffic routing</p><h2>{balancerLabel}</h2><p>Only online Website nodes in the web pool are eligible. Choose a policy and optional router below.</p></div></div>
      <div className="fleetControlFields">
        <label className="fleetSwitchRow"><input type="checkbox" checked={balanceEnabled} disabled={!data || busy} onChange={(event) => updateBalancer({ load_balancing_enabled: event.target.checked })}/><span aria-hidden="true"/><span><b>Load balancer</b><small>{balanceEnabled ? 'Accepting traffic' : 'Not accepting traffic'}</small></span></label>
        <label className="fleetField"><span>Routing strategy</span><select value={data?.load_balancer.strategy || 'least-load'} disabled={!data || busy} onChange={(event) => updateBalancer({ strategy: event.target.value })}><option value="least-load">Least load</option><option value="round-robin">Round robin</option></select></label>
        <label className="fleetField"><span>Router node</span><select value={data?.load_balancer.router_server_uuid || ''} disabled={!data || busy} onChange={(event) => updateBalancer({ router_server_uuid: event.target.value })}><option value="">Choose automatically</option>{data?.nodes.filter((node) => node.role_router).map((node) => <option key={node.uuid} value={node.uuid}>{node.name}</option>)}</select></label>
      </div>
    </section>

    <section className="fleetStats" aria-label="Fleet summary">{[['Servers', data?.summary.total_nodes || 0, Server], ['Reporting', data?.summary.online_nodes || 0, Activity], ['Website pool', data?.summary.website_nodes || 0, Network], ['Background', data?.summary.worker_nodes || 0, Cpu]].map(([label, value, Icon]) => { const IconComponent = Icon as typeof Server; return <article key={String(label)}><IconComponent size={16}/><span>{String(label)}</span><strong>{String(value)}</strong></article>; })}</section>

    <div className="fleetWorkspace">
      <section className="shadcnCard fleetInventory">
        <header className="fleetPanelHeader"><div><p className="eyebrow">Server inventory</p><h2>Nodes and responsibilities</h2><p>Enable one or more roles per node. Changes save immediately.</p></div><button className="shadcnOutline fleetRefresh" type="button" onClick={fleet.reload} disabled={fleet.loading || busy}><Activity size={15}/><span>Refresh</span></button></header>
        <div className="fleetNodeList">{fleet.loading ? <p className="fleetLoading">Loading fleet records…</p> : data?.nodes.length ? data.nodes.map((node) => {
          const percent = node.load_percent === null ? 0 : Math.max(0, Math.min(100, node.load_percent));
          const status = node.load_percent === null ? 'Waiting for first report' : `${Math.round(percent)}% current load`;
          return <article className="fleetNodeRow" key={node.uuid}>
            <div className="fleetNodeIdentity"><span className={`fleetStatusDot ${node.status}`} aria-hidden="true"/><div><h3>{node.name}</h3><p>{node.host} <span aria-hidden="true">·</span> {node.server_type}</p></div></div>
            <div className="fleetNodeLoad"><div><span>Node load</span><strong>{status}</strong></div><div className="fleetBar" aria-label={status}><span style={{ width: `${percent}%` }}/></div></div>
            <div className="fleetNodeRoles" aria-label={`${node.name} workload roles`}>
              <span>Roles</span><div>
                <button type="button" aria-pressed={node.role_websites} className={node.role_websites ? 'selected' : ''} disabled={busy} onClick={() => updateRoles(node, { role_websites: !node.role_websites, ...(node.role_websites ? { load_balancing_enabled: false } : {}) })}>Websites</button>
                <button type="button" aria-pressed={node.role_router} className={node.role_router ? 'selected' : ''} disabled={busy} onClick={() => updateRoles(node, { role_router: !node.role_router })}>Router</button>
                <button type="button" aria-pressed={node.role_workers} className={node.role_workers ? 'selected' : ''} disabled={busy} onClick={() => updateRoles(node, { role_workers: !node.role_workers })}>Background</button>
              </div>
            </div>
            <div className="fleetNodeActions"><label className="nodePoolToggle"><input type="checkbox" checked={node.load_balancing_enabled} disabled={busy || !node.role_websites} onChange={(event) => updateRoles(node, { load_balancing_enabled: event.target.checked })}/><span aria-hidden="true"/>In web pool</label><button className="shadcnOutline fleetScriptButton" type="button" disabled={busy} onClick={() => showScript(node)}><TerminalSquare size={15}/>Setup script</button></div>
          </article>;
        }) : <div className="fleetEmpty"><Server size={26}/><h3>No servers enrolled</h3><p>Add a server to create a website, router, or background-workload pool.</p><button className="shadcnPrimary" type="button" onClick={() => setShowEnroll(true)}><Plus size={16}/>Add first server</button></div>}</div>
      </section>

      <aside className="shadcnCard fleetSetupPanel">
        <div className="fleetSetupIcon"><Server size={19}/></div><p className="eyebrow">Server enrollment</p><h2>Add a node</h2><p>Enter a reachable host. The generated setup script gives the node a one-time, scoped enrollment credential.</p>
        {!showEnroll ? <button className="shadcnPrimary fleetSetupOpen" type="button" onClick={() => setShowEnroll(true)}><Plus size={16}/>Add server</button> : <form className="fleetEnroll" onSubmit={enroll}><label htmlFor="fleet-node-name">Node name<input id="fleet-node-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="beta-web-01" maxLength={120} required/></label><label htmlFor="fleet-node-host">IP address or host<input id="fleet-node-host" value={host} onChange={(event) => setHost(event.target.value)} placeholder="203.0.113.10" maxLength={255} required/></label><label htmlFor="fleet-node-type">Server type<select id="fleet-node-type" value={serverType} onChange={(event) => setServerType(event.target.value)}><option value="micro">Micro server</option><option value="vps">VPS</option><option value="dedicated">Dedicated</option><option value="edge">Edge / router</option><option value="build">Build worker</option></select></label><div className="fleetEnrollActions"><button className="shadcnOutline" type="button" onClick={() => setShowEnroll(false)} disabled={busy}>Cancel</button><button className="shadcnPrimary" disabled={busy} type="submit"><Plus size={16}/>{busy ? 'Enrolling…' : 'Enroll node'}</button></div></form>}
      </aside>
    </div>

    {script && <div className="fleetDialogBackdrop" role="presentation" onMouseDown={() => setScript(null)}><section className="fleetScriptDialog shadcnCard" role="dialog" aria-modal="true" aria-labelledby="fleet-script-title" onMouseDown={(event) => event.stopPropagation()}><header className="fleetScriptHeader"><div><p className="eyebrow">Secure enrollment helper</p><h2 id="fleet-script-title">{script.name}</h2><p>Copy the command, review it, then run it as root on this node.</p></div><button className="iconButton" type="button" onClick={() => setScript(null)} aria-label="Close helper script"><X size={17}/></button></header><pre tabIndex={0}>{script.code}</pre><footer className="fleetScriptActions"><button className="shadcnOutline" type="button" onClick={() => setScript(null)}>Cancel</button><button className="shadcnPrimary" type="button" onClick={copyScript}><Copy size={16}/>Copy script</button></footer></section></div>}
  </section>;
}

function PlatformPage({ page }: { page: string }) {
  const payload = useApi<PagePayload>(`/platform/navigation/${page}`);
  const resources = payload.data?.resources || [];
  return <><PageHeader eyebrow="Platform resource" title={payload.data?.title || page} description={payload.data?.description || 'Loading the current platform workspace.'} action={payload.reload}/><section className="legacyPanel compact"><div><p className="eyebrow">Live records</p><h2>{payload.data?.resource_count || 0} resources</h2><p>{payload.error || 'This page is served by the existing FastAPI platform API.'}</p></div></section><section className="resourceList">{resources.length ? resources.slice(0, 12).map((row, index) => <article key={String(row.uuid || index)}><span><strong>{String(row.name || row.title || row.uuid || 'Resource')}</strong><small>{String(row.status || row._table || 'tracked')}</small></span><ChevronRight size={16}/></article>) : <p className="empty">No resources are configured yet.</p>}</section></>;
}

function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: () => void }) { return <header className="pageHeader"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div>{action && <button onClick={action} className="iconButton" aria-label="Refresh"><Activity size={17}/></button>}</header>; }
function InfoCard({ icon: Icon, title, body }: { icon: typeof Activity; title: string; body: string }) { return <article className="infoCard"><Icon size={20}/><h3>{title}</h3><p>{body}</p></article>; }

export default function Shell() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [accountSession, setAccountSession] = useState<AccountSession | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const page = useMemo(() => pathname === '/' ? 'home' : pathname.slice(1), [pathname]);
  useEffect(() => { api<AccountSession>('/auth/session').then((session) => { setAccountSession(session); setOperatorCsrfToken(session.csrf_token || null); }).catch(() => setAccountSession({ authenticated: false })).finally(() => setAuthLoading(false)); }, []);
  const signOut = async () => { try { await api('/auth/session', { method: 'DELETE' }); } finally { setOperatorCsrfToken(null); setAccountSession({ authenticated: false }); } };
  if (authLoading) return <main className="authBoot">Loading secure workspace…</main>;
  if (!accountSession?.authenticated || !accountSession.account) return <LoginScreen onAuthenticated={setAccountSession}/>;
  const account = accountSession.account;
  const content = page === 'home' ? <HomePage/> : page === 'projects' ? <ProjectsPage/> : page === 'overview' ? <OverviewPage/> : page === 'docker' ? <PlatformPage page="docker"/> : page === 'servers' ? <RemoteServersPage/> : page === '9router' ? <RouterPage/> : page === 'settings' ? <SettingsPage/> : page === 'profile' ? <AccountProfilePage account={account} onAccountChange={(updated) => setAccountSession((current) => current ? { ...current, account: updated } : current)} onSignOut={signOut}/> : page === 'users' ? <UsersPage/> : <BlankPage/>;
  return <main className="appShell"><button className="menuButton" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu size={21}/></button><aside className={open ? 'sidebar visible' : 'sidebar'}><button className="closeButton" onClick={() => setOpen(false)} aria-label="Close navigation"><X size={20}/></button><Navigation onNavigate={() => setOpen(false)}/></aside><div className="appAccountCorner"><AccountAvatar account={account} compact/><button onClick={() => window.location.assign('/profile')} aria-label="Open profile">{account.display_name || account.email}</button></div><div className="content">{content}</div></main>;
}
