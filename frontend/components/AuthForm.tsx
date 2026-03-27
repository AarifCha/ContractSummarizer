"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { login, register } from "@/lib/api";
import { setSession } from "@/lib/session";

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const isLogin = mode === "login";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    const response = isLogin ? await login(email, password) : await register(email, password);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(data.detail ?? "Authentication failed");
      return;
    }

    setSession(data.token, data.email);
    router.push("/library");
  }

  return (
    <main className="container" style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: "24px 0" }}>
      <div className="card" style={{ width: "min(460px, 94vw)", padding: 28, background: "var(--surface)" }}>
        <span
          style={{
            display: "inline-block",
            border: "1px solid var(--border)",
            borderRadius: 999,
            padding: "4px 10px",
            color: "var(--muted)",
            fontSize: 12
          }}
        >
          {isLogin ? "Welcome back" : "Get started"}
        </span>
        <h1 style={{ margin: "12px 0 8px", fontSize: 34 }}>{isLogin ? "Sign in" : "Create account"}</h1>
        <p className="muted" style={{ marginTop: 0 }}>Contract Intelligence Workspace</p>
        <form onSubmit={onSubmit} style={{ display: "grid", gap: 10 }}>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" />
          <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" />
          {error && <p style={{ color: "#ffb3b3", margin: 0 }}>{error}</p>}
          <button className="btnPrimary" style={{ fontWeight: 700 }} type="submit">
            {isLogin ? "Sign in" : "Register"}
          </button>
        </form>
        <p className="muted" style={{ marginBottom: 0 }}>
          {isLogin ? "No account?" : "Already have account?"}{" "}
          <Link href={isLogin ? "/register" : "/login"}>{isLogin ? "Register" : "Sign in"}</Link>
        </p>
      </div>
    </main>
  );
}
