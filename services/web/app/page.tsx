import styles from "./page.module.css";

interface HealthResponse {
  status: "ok" | "error";
  database: string;
}

async function fetchHealth(): Promise<HealthResponse | null> {
  const apiUrl = process.env["API_URL"] ?? "http://localhost:8000";
  try {
    const response = await fetch(`${apiUrl}/health`, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as HealthResponse;
  } catch {
    return null;
  }
}

export default async function Home() {
  const health = await fetchHealth();

  return (
    <div className={styles["page"]}>
      <main className={styles["main"]}>
        <h1>Lanterne</h1>
        <p>Hello-world — validates the API ↔ PostgreSQL ↔ web chain.</p>
        {health === null ? (
          <p>API unreachable.</p>
        ) : (
          <p>
            API status: <strong>{health.status}</strong> — database:{" "}
            <strong>{health.database}</strong>
          </p>
        )}
      </main>
    </div>
  );
}
