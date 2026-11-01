import { type FormEvent, useState } from "react";
import { Navigate } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { ApiError, changePassword } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { Field } from "./Field";

export function PasswordPage() {
  const demo = useMe().data?.demo === true;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  // The demo user has no password to change, and the server refuses it anyway.
  if (demo) return <Navigate to="/settings" replace />;

  return (
    <>
      <PageHeader title="Change password" />
      <Card className="max-w-md">
        <form className="flex flex-col gap-4" onSubmit={onSubmit} aria-label="Change password">
          <Field
            label="Current password"
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <Field
            label="New password"
            type="password"
            autoComplete="new-password"
            required
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
          <Field
            label="Confirm new password"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {error && (
            <p role="alert" className="m-0 text-sm text-bad">
              {error}
            </p>
          )}
          {done && (
            <p role="status" className="m-0 text-sm text-good">
              Password changed.
            </p>
          )}
          <Button type="submit" variant="primary" disabled={busy} className="self-start">
            Change password
          </Button>
        </form>
      </Card>
    </>
  );
}
