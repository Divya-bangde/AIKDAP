import { motion } from "motion/react";
import { ArrowDown, Brain, FileText, Lightbulb, Quote } from "lucide-react";
import { useState } from "react";
import { Link, Navigate } from "react-router-dom";

import { AikdapMark } from "@/components/common/AikdapMark";
import { LoginForm } from "@/features/auth/LoginForm";
import { RegisterForm } from "@/features/auth/RegisterForm";
import { EntryBackdrop } from "@/features/landing/EntryBackdrop";
import { loginBrandReveal, loginWordmark, staggerContainer, staggerItem } from "@/lib/motion";
import { useAuthStore } from "@/store/auth-store";

/** What the platform does, in four steps. A description of the
 * product's purpose — not a live feed, and not backed by any data. */
const FLOW = [
  { icon: FileText, label: "Documents", detail: "Your own sources, extracted and chunked" },
  { icon: Brain, label: "Knowledge", detail: "Understood and embedded for retrieval" },
  { icon: Quote, label: "Intelligence", detail: "Reranked, gated, and grounded in evidence" },
  { icon: Lightbulb, label: "Decision", detail: "Answers you can trace back to a source" },
];

export function Login() {
  const accessToken = useAuthStore((state) => state.accessToken);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [registeredEmail, setRegisteredEmail] = useState<string | null>(null);

  if (accessToken) {
    return <Navigate to="/" replace />;
  }

  return (
    /* The entry experience's own backdrop, rendered behind the whole
     * screen (Sprint 9K.4, Phase 17). Arriving from the landing page,
     * the background does not change — the same aurora, the same grid,
     * the same vignette are already painted — so the eye reads the
     * handoff as one continuous surface with the content swapping on
     * top of it, rather than as two unrelated pages. The layers are
     * token-driven, so this is equally true for a light-theme user.
     *
     * That continuity is carried by a shared, static backdrop and a
     * shared wordmark rather than by an animation across the auth
     * boundary. A cross-route `AnimatePresence` was considered and
     * rejected: it has to keep the *outgoing* route mounted while it
     * exits, and an outgoing protected route with a cleared session
     * redirects itself mid-exit. A transition that fights the router
     * over the address bar is worse than no transition. */
    <main className="relative flex min-h-screen overflow-hidden">
      <EntryBackdrop vignette={false} animated />

      {/* Brand panel. Hidden below `lg` so the form is never pushed
       * below the fold on a laptop or phone — the entry experience must
       * never get between a user and signing in. */}
      <motion.div
        initial="hidden"
        animate="visible"
        variants={loginBrandReveal}
        className="relative z-10 hidden w-[46%] flex-col justify-between overflow-hidden p-12 lg:flex"
      >
        <div className="relative">
          <Link
            to="/"
            className="inline-flex items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <AikdapMark className="h-11 w-11 rounded-xl shadow-raised" />
            <span>
              {/* The wordmark arrives fractionally larger and settles —
               * the visual echo of the landing page's oversized
               * AIKDAP contracting into the application's scale. */}
              <motion.h1 variants={loginWordmark} className="font-display text-title">
                AIKDAP
              </motion.h1>
              <span className="text-label uppercase text-muted-foreground">
                Intelligence Platform
              </span>
            </span>
          </Link>

          <p className="mt-6 max-w-sm text-sm leading-relaxed text-muted-foreground">
            AI-driven Knowledge Discovery &amp; Analysis Platform. AIKDAP completes research
            work — it doesn't just answer questions.
          </p>
        </div>

        <motion.ol
          variants={staggerContainer}
          className="relative flex flex-1 flex-col justify-center gap-1 py-10"
        >
          {FLOW.map(({ icon: Icon, label, detail }, index) => (
            <motion.li key={label} variants={staggerItem}>
              <div className="flex items-start gap-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-background text-ai shadow-subtle">
                  <Icon className="h-4 w-4" />
                </div>
                <div className="pt-1.5">
                  <p className="text-sm font-medium leading-tight">{label}</p>
                  <p className="mt-0.5 text-xs leading-tight text-muted-foreground">{detail}</p>
                </div>
              </div>
              {index < FLOW.length - 1 && (
                <ArrowDown
                  aria-hidden="true"
                  className="my-1 ml-[1.15rem] h-3.5 w-3.5 text-muted-foreground/40"
                />
              )}
            </motion.li>
          ))}
        </motion.ol>

        <motion.p variants={staggerItem} className="relative text-xs italic text-muted-foreground">
          "Turn documents into grounded research."
        </motion.p>
      </motion.div>

      <div className="relative z-10 flex flex-1 items-center justify-center p-6">
        {/* The sign-in surface arrives after the brand side has
         * settled, so the eye is led from the identity it just carried
         * over from the landing page to the thing it now has to do. */}
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.12, ease: [0.22, 1, 0.36, 1] }}
          className="w-full max-w-sm"
        >
          <div className="mb-8 flex flex-col items-center gap-3 text-center lg:hidden">
            <AikdapMark className="h-11 w-11 rounded-xl" />
            <div>
              {/* The desktop brand panel's own `h1` is `display: none`
               * below `lg`, so without this the page had no level-one
               * heading at all on a phone — axe flagged it at 390px.
               * Both exist in the DOM at wide viewports, which is
               * valid, and only one is ever rendered. */}
              <h1 className="font-display text-title">AIKDAP</h1>
              <p className="text-sm text-muted-foreground">
                AI-driven Knowledge Discovery &amp; Analytics Platform
              </p>
            </div>
          </div>

          <div className="rounded-card bg-card p-7 shadow-raised contrast-more:border contrast-more:border-border-strong">
            <div className="mb-6">
              <h2 className="text-section">
                {mode === "login" ? "Sign in" : "Create your account"}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {mode === "login"
                  ? "Continue to your intelligence workspace."
                  : "Start building a grounded knowledge base."}
              </p>
            </div>

            {mode === "login" ? (
              <>
                <LoginForm />
                {registeredEmail && (
                  <p className="mt-4 text-center text-sm text-success">
                    Account created for {registeredEmail}. Sign in above.
                  </p>
                )}
                <p className="mt-6 text-center text-sm text-muted-foreground">
                  Don&apos;t have an account?{" "}
                  <button
                    type="button"
                    onClick={() => setMode("register")}
                    className="font-medium text-primary hover:underline"
                  >
                    Register
                  </button>
                </p>
              </>
            ) : (
              <>
                <RegisterForm
                  onRegistered={(email) => {
                    setRegisteredEmail(email);
                    setMode("login");
                  }}
                />
                <p className="mt-6 text-center text-sm text-muted-foreground">
                  Already have an account?{" "}
                  <button
                    type="button"
                    onClick={() => setMode("login")}
                    className="font-medium text-primary hover:underline"
                  >
                    Sign in
                  </button>
                </p>
              </>
            )}
          </div>
        </motion.div>
      </div>
    </main>
  );
}
