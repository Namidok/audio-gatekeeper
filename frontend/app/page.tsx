"use client";

// Step 22: a temporary page that only checks the frontend can talk to the backend.
// Steps 23-25 replace it with the real dashboard.
import { useEffect, useState } from "react";

import { checkHealth } from "@/lib/api";

export default function Home() {
  const [status, setStatus] = useState("Checking the API…");

  useEffect(() => {
    checkHealth()
      .then((h) => setStatus(`API: ${h.status} · Redis: ${h.redis ? "connected" : "not connected"}`))
      .catch((err: Error) => setStatus(`Error: ${err.message}`));
  }, []);

  return (
    <main style={{ padding: 48, fontFamily: "system-ui, sans-serif" }}>
      <h1>AI Audio Quality Gatekeeper</h1>
      <p>{status}</p>
    </main>
  );
}
