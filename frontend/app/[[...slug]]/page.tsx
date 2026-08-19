'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Activity, Bot, Boxes, BrainCircuit, CalendarClock, ChevronRight, CircleHelp, Cloud,
  Database, FileCog, Gauge, Home, KeyRound, LayoutDashboard, Menu, Router,
  Server, Settings2, ShieldCheck, Sparkles, TerminalSquare, UsersRound, X,
} from 'lucide-react';
import { api } from '@/lib/api';

type NavItem = { href: string; label: string; icon: typeof Home; apiPage?: string };
type MetricData = { cpu_percent?: number; memory_percent?: number; disk_percent?: number; api_requests?: number; internet_ping_ms?: number; project_count?: number; security_blocked_users?: number };
type Resource = Record<string, unknown>;
type PagePayload = { title?: string; description?: string; resource_count?: number; resources?: Resource[] };

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

function BlankPage() {
  return <section className="intentionalBlank" aria-label="Blank workspace"/>;
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
  const page = useMemo(() => pathname === '/' ? 'home' : pathname.slice(1), [pathname]);
  const content = page === 'home' ? <HomePage/> : page === 'docker' ? <PlatformPage page="docker"/> : page === '9router' ? <RouterPage/> : page === 'settings' ? <SettingsPage/> : page === 'users' ? <UsersPage/> : <BlankPage/>;
  return <main className="appShell"><button className="menuButton" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu size={21}/></button><aside className={open ? 'sidebar visible' : 'sidebar'}><button className="closeButton" onClick={() => setOpen(false)} aria-label="Close navigation"><X size={20}/></button><Navigation onNavigate={() => setOpen(false)}/></aside><div className="content">{content}</div></main>;
}
