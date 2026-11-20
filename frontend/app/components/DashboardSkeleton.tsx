// Shown while the dashboard loads in the browser (also the prerendered HTML).
import styles from "./Dashboard.module.css";

export default function DashboardSkeleton() {
  return (
    <div className={styles.page} aria-busy="true">
      <span className="visually-hidden">Loading the dashboard</span>
      <div style={{ padding: "20px 0", borderBottom: "1px solid var(--line)" }}>
        <div className={styles.skeleton} style={{ width: 220, height: 28 }} />
      </div>
      <div className={styles.main} style={{ paddingTop: 20 }}>
        <div className={styles.skeleton} style={{ height: 180, borderRadius: 12 }} />
        <div className={styles.skeleton} style={{ height: 120, borderRadius: 12 }} />
      </div>
    </div>
  );
}
