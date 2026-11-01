import { type FormEvent, useState } from "react";
import { Navigate, useLocation } from "react-router";
import { Button } from "../../components/ui/button";
import { ApiError, login } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { markModeChanged } from "../../lib/demo";
import { replacePage } from "../../lib/reload";
import { Field } from "./Field";

function safeFrom(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from;
  // Only same-app paths. Never a full URL.
  return typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/";
}

export function LoginPage() {
  const me = useMe();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // A demo session stays on the form: signing in clears its cookie.
  if (me.data && me.data.demo !== true) return <Navigate to={safeFrom(location.state)} replace />;

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      setPassword("");
      // A whole new page, so nothing from before (demo or another user) stays in memory.
      markModeChanged("real");
      replacePage(safeFrom(location.state));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-start justify-center bg-page px-4 pt-[12vh]">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <span
            aria-hidden="true"
            className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-sm font-semibold text-on-primary"
          >
            JF
          </span>
          <span className="text-lg font-semibold text-fg">Your household</span>
        </div>
        <section className="rounded-card bg-card p-6 shadow-card">
          <h1 className="m-0 mb-5 text-xl font-medium text-fg">Sign in</h1>
          <form onSubmit={onSubmit} aria-label="Sign in" className="flex flex-col gap-4">
            <Field
              label="Username"
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <Field
              label="Password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error && (
              <p role="alert" className="m-0 text-sm text-bad">
                {error}
              </p>
            )}
            <Button type="submit" variant="primary" disabled={busy} className="mt-1 w-full justify-center">
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </section>
      </div>
    </main>
  );
}
