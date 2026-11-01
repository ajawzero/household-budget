import { useEffect } from "react";
import { Link } from "react-router";

/**
 * Where demo ends: Leave, a 401 in demo, or a restart. It names no household,
 * and the sign-in form is one tap away, not filled in under a friend's thumb.
 */
export function DemoEndedPage() {
  useEffect(() => {
    document.title = "Demo ended · Budget";
    return () => {
      document.title = "Budget";
    };
  }, []);

  return (
    <main className="flex min-h-dvh items-start justify-center bg-page px-4 pt-[12vh]">
      <section className="w-full max-w-sm rounded-card bg-card p-6 shadow-card">
        <h1 className="m-0 mb-2 text-xl font-medium text-fg">Demo ended</h1>
        <p className="m-0 mb-5 text-sm text-fg-secondary">The sample household is gone from this browser. Sign in to see your own data.</p>
        <Link
          to="/login"
          className="inline-flex h-11 w-full md:h-9 items-center justify-center rounded-lg bg-primary px-4 text-sm font-medium text-on-primary no-underline hover:bg-primary-hover"
        >
          Sign in
        </Link>
      </section>
    </main>
  );
}
