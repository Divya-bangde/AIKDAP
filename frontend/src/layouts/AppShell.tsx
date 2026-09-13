import { motion } from "motion/react";
import {
  ArrowLeft,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
} from "lucide-react";
import { useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";

import { AikdapMark } from "@/components/common/AikdapMark";
import { ThemeToggle } from "@/components/common/ThemeToggle";
import { ProfilePersonaSelect } from "@/features/auth/ProfilePersonaSelect";
import { ActiveWorkToast } from "@/features/command-center/ActiveWorkToast";
import { EntryBackdrop } from "@/features/landing/EntryBackdrop";
import { RoutedPage } from "@/layouts/RoutedPage";
import { cn } from "@/lib/utils";
import { layoutSpring } from "@/lib/motion";
import { useAuth } from "@/hooks/useAuth";

const NAV_ITEMS: { to: string; label: string; icon: typeof LayoutDashboard; end: boolean }[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/projects", label: "Projects", icon: FolderKanban, end: false },
  { to: "/research", label: "Research", icon: Search, end: false },
];

const COLLAPSED_KEY = "aikdap-sidebar-collapsed";

/** Storage can throw (private mode, blocked site data); the sidebar
 * then simply starts expanded. */
function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/** Up to two initials from a name or an email address. */
function initials(name: string): string {
  return name
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

function NavItems({ compact = false, collapsed = false }: { compact?: boolean; collapsed?: boolean }) {
  const location = useLocation();

  return (
    <>
      {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => {
        const isActive = end
          ? location.pathname === to
          : location.pathname.startsWith(to);

        return (
          <NavLink
            key={to}
            to={to}
            end={end}
            title={collapsed ? label : undefined}
            className={cn(
              "press group relative flex shrink-0 items-center gap-3 rounded-md font-medium",
              compact ? "px-3 py-1.5 text-xs" : "px-3 py-2 text-sm",
              collapsed && "justify-center px-0",
              isActive ? "text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {/* One shared `layoutId` across all links, so Motion animates
             * a single indicator sliding between them rather than
             * cross-fading separate backgrounds. */}
            {isActive && (
              <motion.span
                layoutId={compact ? "nav-indicator-compact" : "nav-indicator"}
                transition={layoutSpring}
                className="absolute inset-0 rounded-md bg-accent"
              />
            )}
            <Icon
              className={cn(
                "relative z-10 transition-transform duration-200 group-hover:scale-110",
                compact ? "h-3.5 w-3.5" : "h-4 w-4",
                isActive && "text-accent-foreground",
              )}
            />
            <span
              className={cn(
                collapsed ? "sr-only" : "relative z-10",
                isActive && "text-accent-foreground",
              )}
            >
              {label}
            </span>
          </NavLink>
        );
      })}
    </>
  );
}

/** The application shell every protected route renders inside — one
 * sidebar, one topbar, and the routed page in between. Built once so
 * navigation, branding, and logout live in exactly one place. */
export function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(readCollapsed);

  const displayName = user?.full_name || user?.email || "—";
  const segments = location.pathname.split("/").filter(Boolean);
  // Only inner pages (a project, a research run) have somewhere to go back to.
  const canGoBack = segments.length > 1;

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
    } catch {
      // Storage blocked: the sidebar still collapses for this visit.
    }
  }

  /** A page opened in a fresh tab has no in-app history; react-router's
   * `idx` says whether there is one, otherwise go to the parent list. */
  function goBack() {
    if ((window.history.state?.idx ?? 0) > 0) navigate(-1);
    else navigate(`/${segments[0]}`);
  }

  /** Signing out returns the user to the public entry experience, not
   * to a login form. `/` is the landing page for anyone without a
   * session — including after a reload, since the route is chosen from
   * the persisted auth store rather than from any "just logged out"
   * flag. */
  function handleLogout() {
    logout();
    navigate("/", { replace: true });
  }

  const CollapseIcon = collapsed ? PanelLeftOpen : PanelLeftClose;

  return (
    <div className="flex min-h-screen bg-background">
      {/* The entry experience's backdrop (colour washes and grid), so the
       * workspace reads as the same place as the landing and sign-in
       * pages. No vignette, as on sign-in: it is tuned for the always-dark
       * landing and would muddy the light theme. */}
      <EntryBackdrop vignette={false} />
      <aside
        className={cn(
          "sticky top-0 hidden h-screen shrink-0 flex-col bg-card transition-[width] duration-200 md:flex print:hidden",
          collapsed ? "w-[72px]" : "w-64",
        )}
      >
        <div
          className={cn(
            "flex h-16 items-center",
            collapsed ? "justify-center px-2" : "px-5",
          )}
        >
          <Link
            to="/home"
            aria-label="AIKDAP home"
            title="AIKDAP home"
            className="flex min-w-0 items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <AikdapMark className="h-9 w-9 shrink-0 rounded-lg shadow-subtle" />
            {!collapsed && (
              <span className="font-display text-xl font-semibold tracking-tight">AIKDAP</span>
            )}
          </Link>
        </div>

        <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-3">
          {!collapsed && (
            <p className="px-3 pb-2 pt-3 text-label uppercase text-muted-foreground">Workspace</p>
          )}
          <NavItems collapsed={collapsed} />
        </nav>

        <div className="flex flex-col gap-2 p-3">
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={cn(
              "press flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground",
              collapsed && "justify-center px-0",
            )}
          >
            <CollapseIcon className="h-4 w-4" />
            {!collapsed && <span>Collapse</span>}
          </button>

          <div
            className={cn(
              "flex items-center gap-3 rounded-lg bg-sunken p-2",
              collapsed && "flex-col gap-2",
            )}
          >
            <span
              aria-hidden="true"
              title={displayName}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground"
            >
              {initials(displayName)}
            </span>
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{displayName}</p>
                {user?.full_name && (
                  <p className="truncate text-xs text-muted-foreground">{user.email}</p>
                )}
                {user && <ProfilePersonaSelect persona={user.persona} />}
              </div>
            )}
            <button
              type="button"
              onClick={handleLogout}
              aria-label="Log out"
              title="Log out"
              className="press flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-destructive"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* `min-w-0` is load-bearing, not cosmetic. A flex item defaults to
       * `min-width: auto`, which means this column refuses to shrink below
       * its content's intrinsic minimum — so on a phone every
       * authenticated page scrolled sideways (Sprint 9K.6). Zeroing it
       * hands overflow back to the children built for it. `relative`
       * lifts the column above the fixed `EntryBackdrop`. */}
      <div className="relative flex min-h-screen min-w-0 flex-1 flex-col">
        {/* Floating chrome, not a fixed strip: content scrolls beneath
            it and fades out under a short gradient rather than being
            cut off by a 1px rule. `scroll-edge-chrome` supplies the
            translucency, the blur and that gradient — see index.css. */}
        <header className="scroll-edge-chrome top-0 z-30 flex h-16 items-center justify-between gap-2 px-4 md:px-8 print:hidden">
          <div className="flex items-center gap-2">
            <Link to="/home" className="font-display text-base font-semibold tracking-tight md:hidden">
              AIKDAP
            </Link>
            {canGoBack && (
              <button
                type="button"
                onClick={goBack}
                aria-label="Go back"
                className="press flex items-center gap-1.5 rounded-md px-2.5 py-2 text-sm font-medium text-muted-foreground hover:bg-secondary hover:text-foreground"
              >
                <ArrowLeft className="h-4 w-4" />
                <span className="hidden sm:inline">Back</span>
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            {/* The sidebar (and its logout) is hidden below `md`. */}
            <button
              type="button"
              onClick={handleLogout}
              aria-label="Log out"
              className="press flex items-center rounded-md p-2 text-muted-foreground hover:bg-secondary hover:text-foreground md:hidden"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </header>

        <nav className="flex items-center gap-1 overflow-x-auto bg-card px-4 py-2 md:hidden print:hidden">
          <NavItems compact />
        </nav>

        <main className="relative flex-1 p-4 md:p-8">
          <div className="relative mx-auto w-full max-w-[1400px]">
            <RoutedPage />
          </div>
        </main>
      </div>

      <ActiveWorkToast />
    </div>
  );
}
