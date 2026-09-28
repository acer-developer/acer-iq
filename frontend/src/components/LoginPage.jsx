import React, { useState } from "react";
import { supabase } from "../lib/auth.js";

// Email + password via Supabase Auth. "Confirm email" is on in the Supabase
// project, so a new account must click the link in its inbox before it can
// sign in - the screen says so rather than leaving a silent failure.
export default function LoginPage({ recovery = false, onRecovered }) {
  const [mode, setMode] = useState(recovery ? "reset" : "signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else if (mode === "signup") {
        const { error } = await supabase.auth.signUp({
          email, password, options: { emailRedirectTo: window.location.origin },
        });
        if (error) throw error;
        setNotice("Account created. Check your inbox and click the confirmation link, then sign in.");
        setMode("signin");
      } else if (mode === "forgot") {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: window.location.origin,
        });
        if (error) throw error;
        setNotice("If that address has an account, a reset link is on its way.");
      } else if (mode === "reset") {
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw error;
        setNotice("Password updated.");
        onRecovered?.();
      }
    } catch (err) {
      setError(err.message ?? "Something went wrong - try again.");
    } finally {
      setBusy(false);
    }
  };

  const title = { signin: "Sign in", signup: "Create account",
                  forgot: "Reset password", reset: "Choose a new password" }[mode];
  const needsEmail = mode !== "reset";
  const needsPassword = mode !== "forgot";

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="mb-5 flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-600 text-xs font-bold text-white">IQ</div>
          <span className="text-sm font-bold text-gray-900">ACER-IQ</span>
        </div>
        <h1 className="text-base font-semibold text-gray-900">{title}</h1>
        <p className="mt-1 text-xs text-gray-500">
          Each BD has their own pipeline. Sign in with your work email.
        </p>

        {needsEmail && (
          <label className="mt-4 block text-xs font-medium text-gray-600">
            Email
            <input type="email" required autoComplete="email" value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none" />
          </label>
        )}
        {needsPassword && (
          <label className="mt-3 block text-xs font-medium text-gray-600">
            {mode === "reset" ? "New password" : "Password"}
            <input type="password" required minLength={8}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              value={password} onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none" />
          </label>
        )}

        {error && <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}
        {notice && <p className="mt-3 rounded-md bg-green-50 px-3 py-2 text-xs text-green-700">{notice}</p>}

        <button type="submit" disabled={busy}
          className="mt-4 w-full rounded-lg bg-blue-600 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-50">
          {busy ? "Working..." : title}
        </button>

        {mode !== "reset" && (
          <div className="mt-4 flex justify-between text-xs">
            {mode === "signin" ? (
              <>
                <button type="button" className="text-blue-600 hover:underline" onClick={() => setMode("signup")}>Create account</button>
                <button type="button" className="text-gray-500 hover:underline" onClick={() => setMode("forgot")}>Forgot password?</button>
              </>
            ) : (
              <button type="button" className="text-blue-600 hover:underline" onClick={() => setMode("signin")}>Back to sign in</button>
            )}
          </div>
        )}
      </form>
    </div>
  );
}
