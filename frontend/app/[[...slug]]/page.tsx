'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Activity, Bot, Boxes, BrainCircuit, CalendarClock, ChevronRight, CircleHelp, Cloud,
  Database, FileCog, Gauge, Home, KeyRound, LayoutDashboard, Menu, Router,
  Server, Settings2, ShieldCheck, Sparkles, TerminalSquare, UsersRound, X, LogOut, Mail, Save, UserRound, Rocket, Leaf, Heart, Camera, LockKeyhole, AtSign, Plus, Copy, Network, HardDrive, Cpu, Check,
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
    event.preventDefault(); if (!name.trim() || !host.trim()) return;
    setBusy(true); setMessage(null);
    try {
      await api('/platform/fleet/servers', { method: 'POST', body: JSON.stringify({ name, host, server_type: serverType, role_websites: true, role_router: serverType === 'edge', role_workers: serverType === 'build', load_balancing_enabled: serverType !== 'build' }) });
      setName(''); setHost(''); setShowEnroll(false); await fleet.reload(); setMessage('Server enrolled. Generate its helper script to start real load reporting.');
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  const showScript = async (node: FleetNode) => {
    setBusy(true); setMessage(null);
    try { const result = await api<{ filename: string; script: string }>(`/platform/fleet/servers/${node.uuid}/setup-script`); setScript({ name: result.filename, code: result.script }); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  const copyScript = async () => { if (!script) return; await navigator.clipboard.writeText(script.code); setMessage('Helper script copied. Review it before running as root on the node.'); };
  const statusText = data?.load_balancer.enabled ? `${data.load_balancer.eligible_targets.length} healthy web target${data.load_balancer.eligible_targets.length === 1 ? '' : 's'} in pool` : 'Load balancing is disabled';
  return <section className="fleetPage">
    <PageHeader eyebrow="Infrastructure fleet" title="Remote Servers" description="Enroll micro-servers, assign workload roles, and route web traffic according to real node-reported load." action={fleet.reload}/>
    {message && <p className="notice fleetNotice">{message}</p>}
    <section className="fleetHeroPanel">
      <div className="fleetBalancerTitle"><div className="fleetIcon"><Network size={23}/></div><div><p className="eyebrow">Load balancer</p><h2>{data?.load_balancer.enabled ? 'Traffic distribution is enabled' : 'Traffic distribution is paused'}</h2><p>{statusText}. The control plane selects eligible Website nodes using the configured policy.</p></div></div>
      <div className="fleetBalancerControls"><label className="switchControl"><input type="checkbox" checked={Boolean(data?.load_balancer.enabled)} disabled={!data || busy} onChange={(event) => updateBalancer({ load_balancing_enabled: event.target.checked })}/><span/><b>{data?.load_balancer.enabled ? 'Enabled' : 'Disabled'}</b></label><label>Strategy<select value={data?.load_balancer.strategy || 'least-load'} disabled={!data || busy} onChange={(event) => updateBalancer({ strategy: event.target.value })}><option value="least-load">Least load</option><option value="round-robin">Round robin</option></select></label><label>Router node<select value={data?.load_balancer.router_server_uuid || ''} disabled={!data || busy} onChange={(event) => updateBalancer({ router_server_uuid: event.target.value })}><option value="">Automatic router selection</option>{data?.nodes.filter((node) => node.role_router).map((node) => <option key={node.uuid} value={node.uuid}>{node.name}</option>)}</select></label></div>
    </section>
    <section className="fleetSummary">{[['Fleet nodes', data?.summary.total_nodes || 0, Server], ['Reporting', data?.summary.online_nodes || 0, Activity], ['Web targets', data?.summary.website_nodes || 0, Network], ['Background', data?.summary.worker_nodes || 0, Cpu]].map(([label, value, Icon]) => { const IconComponent = Icon as typeof Server; return <article key={String(label)}><IconComponent size={18}/><span>{String(label)}</span><strong>{String(value)}</strong></article>; })}</section>
    <div className="fleetSectionHeader"><div><p className="eyebrow">Server inventory</p><h2>Nodes & workload roles</h2></div><button className="darkButton" onClick={() => setShowEnroll((open) => !open)}><Plus size={16}/>{showEnroll ? 'Close enrollment' : 'Add a server'}</button></div>
    {showEnroll && <form className="fleetEnroll" onSubmit={enroll}><label>Node name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="beta-web-01" maxLength={120} required/></label><label>IP address or host<input value={host} onChange={(event) => setHost(event.target.value)} placeholder="203.0.113.10" maxLength={255} required/></label><label>Server type<select value={serverType} onChange={(event) => setServerType(event.target.value)}><option value="micro">Micro server</option><option value="vps">VPS</option><option value="dedicated">Dedicated</option><option value="edge">Edge / router</option><option value="build">Build worker</option></select></label><button className="darkButton" disabled={busy} type="submit"><Plus size={16}/>{busy ? 'Enrolling…' : 'Enroll node'}</button></form>}
    <section className="fleetGrid">{fleet.loading ? <p className="empty">Loading fleet records…</p> : data?.nodes.length ? data.nodes.map((node) => { const percent = node.load_percent === null ? 0 : Math.max(0, Math.min(100, node.load_percent)); return <article className="fleetNode" key={node.uuid}><div className="fleetNodeTop"><div><span className={`fleetState ${node.status}`}>{node.status}</span><h3>{node.name}</h3><p>{node.host} · {node.server_type}</p></div><HardDrive size={20}/></div><div className="fleetLoad"><div><span>Node load</span><strong>{node.load_percent === null ? 'Awaiting report' : `${Math.round(percent)}%`}</strong></div><div className="fleetBar"><span style={{ width: `${percent}%` }}/></div></div><div className="fleetRoles"><button className={node.role_websites ? 'selected' : ''} disabled={busy} onClick={() => updateRoles(node, { role_websites: !node.role_websites, ...(node.role_websites ? { load_balancing_enabled: false } : {}) })}>Websites</button><button className={node.role_router ? 'selected' : ''} disabled={busy} onClick={() => updateRoles(node, { role_router: !node.role_router })}>Router</button><button className={node.role_workers ? 'selected' : ''} disabled={busy} onClick={() => updateRoles(node, { role_workers: !node.role_workers })}>Background</button></div><div className="fleetNodeFooter"><label className="nodePoolToggle"><input type="checkbox" checked={node.load_balancing_enabled} disabled={busy || !node.role_websites} onChange={(event) => updateRoles(node, { load_balancing_enabled: event.target.checked })}/><span/>Web pool</label><button className="lightButton fleetScriptButton" disabled={busy} onClick={() => showScript(node)}><TerminalSquare size={15}/>Helper script</button></div></article>; }) : <article className="fleetEmpty"><Server size={28}/><h3>Start with a server</h3><p>Enroll a micro-server, VPS, router, or build worker to create your deployment fleet.</p></article>}</section>
    {script && <section className="fleetScriptDialog" role="dialog" aria-modal="true" aria-label="Server helper script"><div className="fleetScriptHeader"><div><p className="eyebrow">Secure node enrollment</p><h2>{script.name}</h2><p>Copy this node-specific helper, review it, then run it as root on the enrolled server.</p></div><button className="iconButton" onClick={() => setScript(null)} aria-label="Close helper script"><X size={17}/></button></div><pre>{script.code}</pre><div className="fleetScriptActions"><button className="lightButton" onClick={() => setScript(null)}>Close</button><button className="darkButton" onClick={copyScript}><Copy size={16}/>Copy script</button></div></section>}
  </section>;
}

type Preflight = { ok: boolean; blocking?: boolean; project?: { uuid?: string; name?: string; branch?: string; git_url?: string }; detection?: { framework?: string; language?: string; runtime?: string; package_manager?: string; deploy_type?: string; build_command?: string; start_command?: string; dockerfile_path?: string; warnings?: string[]; env_keys?: Array<{ name: string; configured: boolean; source: string }> } };

function ProjectsPage() {
  const projects = useApi<Array<Record<string, any>>>('/workspace_list');
  const [selected, setSelected] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const inspect = async (uuid: string) => { setSelected(uuid); setPreflight(null); try { setPreflight(await api<Preflight>(`/deploy_preflight?uuid=${encodeURIComponent(uuid)}`)); } catch (error) { setPreflight({ ok: false, blocking: true, detection: { warnings: [(error as Error).message] } }); } };
  const deploy = async () => { if (!selected || preflight?.blocking) return; setDeploying(true); setLogs([]); try { const result = await api<{ message?: string }>(`/issue_deployment`, { method: 'POST', body: JSON.stringify({ uuid: selected }) }); setLogs([result.message || 'Deployment started.', 'Watching deployment log stream…']); } catch (error) { setLogs([(error as Error).message]); } finally { setDeploying(false); } };
  const current = projects.data?.find((project) => project.uuid === selected);
  return <><PageHeader eyebrow="Deployment workspace" title="Projects" description="Review detected configuration before deploying, then follow the live deployment timeline." action={projects.reload}/><section className="projectsLayout"><div className="projectList">{projects.loading ? <p className="empty">Loading projects…</p> : projects.data?.length ? projects.data.map((project) => <button className={selected === project.uuid ? 'projectRow selected' : 'projectRow'} key={project.uuid} onClick={() => inspect(project.uuid)}><span><strong>{project.name || project.uuid}</strong><small>{project.git_url || 'Local workspace'} · {project.branch || 'main'}</small></span><span className={`statusPill ${project.status === 'running' ? 'online' : ''}`}>{project.status || 'created'}</span></button>) : <p className="empty">No projects are configured yet.</p>}</div>{selected && <article className="deployReview"><div className="deployReviewHead"><div><p className="eyebrow">{current?.name || selected}</p><h2>Deployment review</h2><p>Syte scans the workspace without exposing secret values.</p></div><Rocket size={28}/></div>{!preflight ? <p className="empty">Inspecting framework, runtime, commands, and environment keys…</p> : <><div className="detectGrid">{[['Framework', preflight.detection?.framework || 'Not detected'], ['Runtime', preflight.detection?.runtime || 'Not detected'], ['Package manager', preflight.detection?.package_manager || '—'], ['Deploy method', preflight.detection?.deploy_type || 'shell']].map(([label, value]) => <div className="detectCard" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><div className="commandReview"><div><span>Build command</span><code>{preflight.detection?.build_command || 'Not configured'}</code></div><div><span>Start command</span><code>{preflight.detection?.start_command || preflight.detection?.dockerfile_path || 'Not configured'}</code></div></div><div className="envReview"><div className="sectionTitle"><span>Environment keys</span><small>Values hidden</small></div>{preflight.detection?.env_keys?.length ? preflight.detection.env_keys.map((env) => <div className="envRow" key={env.name}><span>{env.name}</span><span className={env.configured ? 'envConfigured' : 'envMissing'}>{env.configured ? 'Configured' : 'Review required'}</span></div>) : <p>No environment keys detected.</p>}</div>{preflight.detection?.warnings?.map((warning) => <p className="notice" key={warning}>{warning}</p>)}<button className="darkButton deployAction" disabled={Boolean(preflight.blocking) || deploying} onClick={deploy}><Rocket size={16}/>{deploying ? 'Deploying…' : preflight.blocking ? 'Configuration required' : 'Deploy'}</button>{logs.length > 0 && <div className="deployTimeline" aria-live="polite"><div className="sectionTitle"><span>Deployment timeline</span><small>{deploying ? 'Live' : 'Ready'}</small></div>{logs.map((log, index) => <p key={`${log}-${index}`}><Check size={14}/>{log}</p>)}</div>}</>}</article>}</section></>;
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
