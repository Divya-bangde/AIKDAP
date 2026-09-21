import { MotionConfig } from "motion/react";
import { lazy } from "react";
import { Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "@/components/common/ProtectedRoute";
import { NotFound } from "@/components/ui/not-found-2";
import { useApplyTheme } from "@/hooks/useTheme";
import { AppShell } from "@/layouts/AppShell";
import { Landing } from "@/pages/Landing";
import { Login } from "@/pages/Login";
import { useAuthStore } from "@/store/auth-store";

/* The entry path — landing and login — is imported eagerly, because it
 * is what a first-time visitor actually waits for. Everything behind
 * authentication is split out: a visitor who has not signed in never
 * downloads the dashboard, project workspace or research pipeline.
 * `AppShell` renders the Suspense boundary, so switching pages keeps
 * the sidebar and header on screen rather than blanking the window. */
const Dashboard = lazy(() =>
  import("@/pages/Dashboard").then((m) => ({ default: m.Dashboard })),
);
const Projects = lazy(() =>
  import("@/pages/Projects").then((m) => ({ default: m.Projects })),
);
const ProjectDetail = lazy(() =>
  import("@/pages/ProjectDetail").then((m) => ({ default: m.ProjectDetail })),
);
const Documents = lazy(() =>
  import("@/pages/Documents").then((m) => ({ default: m.Documents })),
);
const Research = lazy(() =>
  import("@/pages/Research").then((m) => ({ default: m.Research })),
);

export function App() {
  // Resolves preference → OS fallback and writes the class onto
  // <html>. Mounted once here so every route, portal and drawer sees
  // the same theme (portals render outside the React tree but inside
  // the same document).
  useApplyTheme();

  const accessToken = useAuthStore((state) => state.accessToken);

  return (
    /* `reducedMotion="user"` makes every Motion animation in the app
     * honour the OS-level `prefers-reduced-motion` setting: transform
     * and layout animations are dropped, opacity is kept, so content
     * still appears but nothing travels. Set once, at the root, rather
     * than re-checked in each component. CSS-driven decoration is
     * disabled separately in `index.css`. */
    <MotionConfig reducedMotion="user">
      <Routes>
        <Route path="/login" element={<Login />} />

        {/* The landing page for everyone, signed in or not — where the
         * sidebar's AIKDAP logo leads. A signed-in user cannot reach it
         * at `/`, which is their Command Center. */}
        <Route path="/home" element={<Landing />} />

        {/* `/` serves two different experiences. Declaring the public
         * landing route only while unauthenticated means an
         * authenticated user falls through to the protected `/` below
         * and lands on the Command Center — so the Command Center keeps
         * its URL and no redirect hop is introduced. The store is
         * reactive, so logging in or out re-resolves this immediately. */}
        {!accessToken && <Route path="/" element={<Landing />} />}

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/projects/:id" element={<ProjectDetail />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/research" element={<Research />} />
            <Route path="/research/:runId" element={<Research />} />
          </Route>
        </Route>

        <Route path="*" element={<NotFound />} />
      </Routes>
    </MotionConfig>
  );
}
