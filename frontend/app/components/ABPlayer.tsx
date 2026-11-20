"use client";

// The A/B deck. Each version keeps its own position, waveform and play button; the live
// spectrum shows whichever one is playing; only one plays at a time.
// Audio path per version: <audio crossOrigin> -> GainNode (level match) -> AnalyserNode -> speakers.
import { useEffect, useRef, useState } from "react";

import { formatTime, matchedGains } from "@/lib/format";
import type { Waveforms } from "@/lib/types";

import styles from "./ABPlayer.module.css";
import Spectrum from "./Spectrum";
import Waveform from "./Waveform";

type Side = "original" | "enhanced";
const SIDES: Side[] = ["original", "enhanced"];
const COLOR_VAR: Record<Side, string> = { original: "--original", enhanced: "--enhanced" };

export interface FreshLinks {
  original: string;
  enhanced: string;
  download: string | null;
}

interface Props {
  originalUrl: string;
  enhancedUrl: string;
  downloadUrl: string | null;
  originalLufs: number | null;
  enhancedLufs: number | null;
  waveforms: Waveforms | null;
  enhancedLabel: string;
  onNeedFreshLinks: () => Promise<FreshLinks | null>;
}

interface AudioGraph {
  context: AudioContext;
  gains: Record<Side, GainNode>;
  analysers: Record<Side, AnalyserNode>;
}

const lufsText = (v: number | null) => (v === null ? "loudness not measured" : `${v.toFixed(1)} LUFS`);

export default function ABPlayer(props: Props) {
  const { originalLufs, enhancedLufs, waveforms, enhancedLabel, onNeedFreshLinks } = props;

  // Keep the first links: new signed links on every poll would reload the players mid-song.
  // They are replaced only when a link has expired (then playback resumes where it was).
  const [urls, setUrls] = useState<Record<Side, string>>({ original: props.originalUrl, enhanced: props.enhancedUrl });
  const [downloadUrl, setDownloadUrl] = useState(props.downloadUrl);
  const audio = useRef<Record<Side, HTMLAudioElement | null>>({ original: null, enhanced: null });
  const graphRef = useRef<AudioGraph | null>(null);
  const playToken = useRef(0);
  const resumeAt = useRef<Record<Side, number | null>>({ original: null, enhanced: null });
  const refreshes = useRef(0);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const [active, setActive] = useState<Side>("enhanced");
  const [playing, setPlaying] = useState(false);
  const [times, setTimes] = useState<Record<Side, number>>({ original: 0, enhanced: 0 });
  const [durations, setDurations] = useState<Record<Side, number>>({ original: 0, enhanced: 0 });
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null);
  const [levelMatch, setLevelMatch] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const INFO: Record<Side, { letter: string; name: string; detail: string }> = {
    original: { letter: "A", name: "Original", detail: `Your upload, untouched. ${lufsText(originalLufs)}` },
    enhanced: { letter: "B", name: enhancedLabel, detail: `What you download. ${lufsText(enhancedLufs)}` },
  };

  function applyGains(on: boolean) {
    const gains = matchedGains(on, originalLufs, enhancedLufs);
    const graph = graphRef.current;
    for (const side of SIDES) {
      const el = audio.current[side];
      if (graph) graph.gains[side].gain.value = gains[side];
      else if (el) el.volume = gains[side]; // without Web Audio, the element volume does the same job
    }
  }

  /** Builds the Web Audio chain once. Returns null (plain playback, no spectrum) if that isn't possible. */
  async function ensureGraph(): Promise<AudioGraph | null> {
    let graph = graphRef.current;
    if (graph && graph.context.state === "closed") graph = null;
    if (!graph) {
      const els = audio.current;
      if (!els.original || !els.enhanced) return null;
      const context = new AudioContext();
      try {
        const build = (el: HTMLAudioElement) => {
          const source = context.createMediaElementSource(el);
          const gain = context.createGain();
          const node = context.createAnalyser();
          node.fftSize = 4096;
          node.smoothingTimeConstant = 0.75;
          node.minDecibels = -95;
          node.maxDecibels = -15;
          source.connect(gain).connect(node).connect(context.destination);
          return { gain, node };
        };
        const a = build(els.original);
        const b = build(els.enhanced);
        graph = { context, gains: { original: a.gain, enhanced: b.gain }, analysers: { original: a.node, enhanced: b.node } };
        graphRef.current = graph;
        applyGains(levelMatch);
      } catch {
        void context.close();
        applyGains(levelMatch);
        return null;
      }
    }
    if (graph.context.state === "suspended") await graph.context.resume();
    return graph;
  }

  /** Plays one version from wherever *that* version was left; the other one pauses. */
  async function play(side: Side) {
    const el = audio.current[side];
    if (!el) return;
    const token = ++playToken.current; // a newer click wins; older ones are ignored
    const graph = await ensureGraph();
    if (token !== playToken.current) return;
    audio.current[side === "original" ? "enhanced" : "original"]?.pause();
    try {
      await el.play();
    } catch (err) {
      if (token !== playToken.current) return;
      const name = err instanceof DOMException ? err.name : "";
      if (name === "AbortError") return; // interrupted by another click: not a problem
      setError(
        name === "NotAllowedError"
          ? "Your browser blocked playback. Press play again."
          : "This version couldn't be played in this browser.",
      );
      return;
    }
    if (token !== playToken.current) return;
    setActive(side);
    setAnalyser(graph?.analysers[side] ?? null);
    setError(null);
  }

  function pause() {
    playToken.current++;
    SIDES.forEach((s) => audio.current[s]?.pause());
  }

  function togglePlay(side: Side) {
    if (playing && active === side) pause();
    else void play(side);
  }

  /** A/B switch: keep playing if we were, but continue from the other version's own position. */
  function switchTo(side: Side) {
    if (playing) void play(side);
    else {
      setActive(side);
      if (graphRef.current) setAnalyser(graphRef.current.analysers[side]);
    }
  }

  function seek(side: Side, fraction: number) {
    const el = audio.current[side];
    if (!el || !durations[side]) return;
    el.currentTime = Math.min(1, Math.max(0, fraction)) * durations[side];
    setTimes((t) => ({ ...t, [side]: el.currentTime }));
  }

  /** A signed link stopped working (usually: it expired). Get fresh ones and resume in place. */
  async function recover(side: Side) {
    if (refreshes.current >= 2) {
      setError(
        `The ${side === "original" ? "original" : "processed"} audio couldn't be loaded. If this keeps happening, the storage bucket's CORS rule may be missing GET.`,
      );
      return;
    }
    refreshes.current += 1;
    for (const s of SIDES) resumeAt.current[s] = audio.current[s]?.currentTime ?? null;
    const fresh = await onNeedFreshLinks();
    if (!fresh) {
      setError("The audio links have expired and couldn't be renewed. Reload the page.");
      return;
    }
    setUrls({ original: fresh.original, enhanced: fresh.enhanced });
    setDownloadUrl(fresh.download);
  }

  async function download() {
    // Links expire: fetch a fresh one at the moment of the click
    const fresh = await onNeedFreshLinks();
    const url = fresh?.download ?? downloadUrl;
    if (url) window.location.assign(url);
    else setError("The download link couldn't be created. Please try again.");
  }

  // Level-match checkbox changes the volumes straight away
  useEffect(() => {
    applyGains(levelMatch);
    // applyGains only reads refs and these values
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [levelMatch, originalLufs, enhancedLufs]);

  // Leaving the page section: pause, and release the audio engine unless we come right back
  useEffect(() => {
    clearTimeout(closeTimer.current);
    const elements = audio.current;
    const timers = closeTimer;
    const graphs = graphRef;
    return () => {
      SIDES.forEach((s) => elements[s]?.pause());
      const graph = graphs.current;
      if (!graph) return;
      void graph.context.suspend().catch(() => {});
      timers.current = setTimeout(() => {
        void graph.context.close().catch(() => {});
        if (graphs.current === graph) graphs.current = null;
      }, 30_000);
    };
  }, []);

  // Keyboard: Space = play/pause, A = original, B = processed (never while typing or with modifiers)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      const target = e.target instanceof Element ? e.target : document.body;
      if (target.closest("input, textarea, select, [contenteditable='true']")) return;
      if (e.key === "a" || e.key === "A") switchTo("original");
      else if (e.key === "b" || e.key === "B") switchTo("enhanced");
      else if (e.key === " " && !target.closest("button, [role='button'], [role='slider'], a")) {
        e.preventDefault();
        togglePlay(active);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className={styles.player} data-active={active}>
      <p className={styles.nowPlaying} style={{ color: `var(${COLOR_VAR[active]})` }} aria-live="polite">
        {playing ? "Playing" : "Selected"}: {INFO[active].letter}, {INFO[active].name}
      </p>

      <div className={styles.spectrumBox}>
        <Spectrum analyser={playing ? analyser : null} colorVar={COLOR_VAR[active]} />
      </div>
      <p className={styles.hint}>
        Live spectrum: bass on the left, treble on the right. A low-quality MP3 leaves the right side empty.
      </p>

      {SIDES.map((side) => {
        const isActive = side === active;
        const progress = durations[side] ? times[side] / durations[side] : 0;
        return (
          <div key={side} className={`${styles.lane} ${styles[side]} ${isActive ? styles.laneActive : ""}`}>
            <div className={styles.laneHeader}>
              <button
                type="button"
                className={styles.play}
                onClick={() => togglePlay(side)}
                // never disabled: mobile browsers often load nothing (not even the length) until play
                aria-label={playing && isActive ? `Pause ${INFO[side].name}` : `Play ${INFO[side].name}`}
              >
                <span aria-hidden="true">{playing && isActive ? "❚❚" : "▶"}</span>
              </button>
              <span className={styles.letter} aria-hidden="true">
                {INFO[side].letter}
              </span>
              <div className={styles.meta}>
                <div className={styles.name}>{INFO[side].name}</div>
                <div className={styles.detail}>{INFO[side].detail}</div>
              </div>
              <span className={styles.time}>
                {formatTime(times[side])} / {formatTime(durations[side])}
              </span>
            </div>
            <Waveform
              peaks={waveforms?.[side] ?? null}
              progress={progress}
              duration={durations[side]}
              colorVar={COLOR_VAR[side]}
              dimmed={!isActive}
              label={INFO[side].name}
              onSeek={(f) => seek(side, f)}
            />
            <audio
              ref={(el) => {
                audio.current[side] = el;
              }}
              src={urls[side]}
              crossOrigin="anonymous"
              preload="metadata"
              onTimeUpdate={(e) => {
                const now = e.currentTarget.currentTime;
                setTimes((t) => ({ ...t, [side]: now }));
              }}
              onLoadedMetadata={(e) => {
                const el = e.currentTarget;
                setDurations((d) => ({ ...d, [side]: el.duration }));
                const resume = resumeAt.current[side];
                if (resume !== null) {
                  el.currentTime = resume;
                  resumeAt.current[side] = null;
                }
              }}
              onPlay={() => {
                setActive(side);
                setPlaying(true);
              }}
              onPause={() => {
                if (side === active) setPlaying(false);
              }}
              onEnded={() => {
                if (side === active) setPlaying(false);
              }}
              onError={() => void recover(side)}
            />
          </div>
        );
      })}

      <div className={styles.options}>
        <label className={styles.check}>
          <input type="checkbox" name="level_match" checked={levelMatch} onChange={(e) => setLevelMatch(e.target.checked)} />
          Match loudness (fair comparison)
        </label>
        <span className={styles.keys}>Keys: Space plays or pauses, A and B switch versions</span>
      </div>

      <div className={styles.downloadRow}>
        <button type="button" className={styles.download} onClick={() => void download()} disabled={!downloadUrl}>
          Download mastered WAV (24-bit)
        </button>
      </div>

      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
