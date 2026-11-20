// What a first-time visitor sees: what will happen to their track, in three real steps.
import styles from "./EmptyState.module.css";

const STEPS = [
  {
    title: "Analyse",
    text: "We measure clipping, missing high frequencies, dynamics and loudness, and give the track a health score.",
  },
  {
    title: "Repair, only if needed",
    text: "Healthy tracks skip repair entirely. Damaged ones get their flattened peaks rebuilt, and lost highs restored where the AI model is available.",
  },
  {
    title: "Master",
    text: "Every track is set to a streaming loudness target with safe peaks, and saved as a 24-bit WAV you can compare and download.",
  },
];

export default function EmptyState({ invalidLink }: { invalidLink: boolean }) {
  return (
    <section className={styles.empty} aria-labelledby="empty-title">
      {invalidLink && (
        <p className={styles.notice} role="alert">
          That link doesn&apos;t point to a job. Upload a track to start a new one.
        </p>
      )}
      <h2 id="empty-title" className={styles.title}>
        What happens to your track
      </h2>
      <ol className={styles.steps}>
        {STEPS.map((step, i) => (
          <li key={step.title} className={styles.step}>
            <span className={styles.number} aria-hidden="true">
              {i + 1}
            </span>
            <div>
              <h3 className={styles.stepTitle}>{step.title}</h3>
              <p className={styles.text}>{step.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
