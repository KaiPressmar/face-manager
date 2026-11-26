import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { API_BASE, apiFetch, readApiError, type RuntimeInfo } from "../../utils/api";
import "./runtime-status.css";

type ProviderState = "idle" | "checking" | "active" | "fallback" | "error";
type ProviderDetails = {
  provider_state?: ProviderState;
  provider_reason?: string;
  provider_timings_ms?: Record<string, number>;
  execution_provider: string;
  compute_mode: "gpu" | "cpu";
  provider_initialized?: boolean;
};
export type RuntimeStatusInfo = RuntimeInfo & ProviderDetails;

const REASONS: Record<string, string> = {
  not_checked: "Die Hardware wird beim ersten Import geprüft.",
  initializing: "Gesichtserkennung wird vorbereitet.",
  checking_cuda: "NVIDIA-Grafik wird mit den Modellen geprüft.",
  checking_intel: "Intel-Grafik wird mit den Modellen geprüft.",
  checking_cpu: "Die Modelle werden für den Prozessor vorbereitet.",
  cuda_active: "NVIDIA CUDA ist verwendbar.",
  intel_active: "Intel-Grafik über OpenVINO ist verwendbar.",
  cpu_only: "Die Anwendung ist für CPU-Betrieb konfiguriert.",
  android_cpu: "Unter Android läuft die Erkennung auf dem Prozessor.",
  gpu_unavailable: "Für die Erkennung steht keine unterstützte Grafikkarte bereit. Der Prozessor ist verwendbar.",
  cuda_probe_failed: "Die NVIDIA-Grafik konnte nicht abgefragt werden. Der Prozessor ist verwendbar.",
  cuda_startup_failed: "NVIDIA CUDA konnte die Modelle nicht starten. Der Prozessor ist verwendbar.",
  intel_startup_failed: "Die Intel-Grafik konnte die Modelle nicht starten. Der Prozessor ist verwendbar.",
  cuda_inference_failed: "Bei der NVIDIA-Grafik trat während der Erkennung ein Fehler auf. Der Prozessor ist verwendbar.",
  intel_inference_failed: "Bei der Intel-Grafik trat während der Erkennung ein Fehler auf. Der Prozessor ist verwendbar.",
  model_assets_failed: "Die Modelldateien konnten nicht vorbereitet werden.",
  cpu_startup_failed: "Die Modelle konnten auf dem Prozessor nicht gestartet werden.",
  cpu_inference_failed: "Bei der Erkennung auf dem Prozessor ist ein Fehler aufgetreten.",
  diagnostic_busy: "Ein Hardwaretest läuft bereits.",
  diagnostic_unsupported: "Auf diesem Gerät ist der Hardwaretest nicht verfügbar.",
  diagnostic_timeout: "Der Hardwaretest wurde nach 30 Sekunden beendet.",
  diagnostic_models_missing: "Der Hardwaretest braucht die Modelldateien des ersten Imports.",
  diagnostic_worker_failed: "Der Hardwaretest konnte nicht abgeschlossen werden.",
};

const PHASES: Record<string, string> = {
  model_assets: "Modelldateien",
  provider_probe: "Verfügbare Beschleunigung",
  runtime_preload: "Laufzeitbibliotheken",
  device_probe: "Geräteprüfung",
  sessions: "Modellstart gesamt",
  detection_session: "Gesichtserkennung laden",
  recognition_session: "Gesichtsvergleich laden",
  prepare: "Modelle vorbereiten",
  warmup: "Probeläufe",
};
const HARDWARE: Record<string, string> = { cuda: "NVIDIA", intel: "Intel", cpu: "CPU" };

function phaseLabel(stage: string): string | undefined {
  const [hardware, ...parts] = stage.split("_");
  if (!HARDWARE[hardware]) return PHASES[stage];
  const phase = PHASES[parts.join("_")];
  return phase ? `${HARDWARE[hardware]} · ${phase}` : undefined;
}

function providerName(details: ProviderDetails) {
  if (details.execution_provider === "CUDAExecutionProvider") return "NVIDIA CUDA";
  if (details.execution_provider === "OpenVINOExecutionProvider") return "Intel-Grafik über OpenVINO";
  return "Prozessor";
}

function Timings({ details }: { details?: ProviderDetails | null }) {
  const timings = Object.entries(details?.provider_timings_ms ?? {}).flatMap(([stage, ms]) => {
    const label = phaseLabel(stage);
    return label ? [{ stage, label, ms }] : [];
  });
  if (timings.length === 0) return null;
  return <details className="runtime-status__timings">
    <summary>Gemessene Zeiten</summary>
    <dl>{timings.map(({ stage, label, ms }) => <div key={stage}>
      <dt>{label}</dt><dd>{Math.round(ms).toLocaleString("de-DE")} ms</dd>
    </div>)}</dl>
  </details>;
}

export default function RuntimeStatus({ runtime, unavailable, route }: { runtime: RuntimeStatusInfo | null; unavailable: boolean; route: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const panelId = useId();
  const [checking, setChecking] = useState(false);
  const [diagnostic, setDiagnostic] = useState<ProviderDetails | null>(null);
  const [diagnosticError, setDiagnosticError] = useState("");
  const state = runtime?.provider_state ?? (runtime?.provider_initialized === false ? "idle" : runtime ? "active" : "checking");
  const mode = !unavailable && (state === "active" || state === "fallback") ? runtime?.compute_mode : undefined;
  const label = unavailable ? "Nicht verfügbar" : state === "idle" ? "Wartet" : state === "checking" ? "Wird geprüft"
    : state === "error" ? "Fehler" : state === "fallback" ? "CPU-Ersatzbetrieb" : (mode?.toUpperCase() ?? "Wird geprüft");
  const reason = unavailable ? "Der aktuelle Hardwarestatus ist nicht erreichbar."
    : mode === "gpu" && runtime ? `Gesichtserkennung verwendet ${providerName(runtime)}.`
    : REASONS[runtime?.provider_reason ?? ""] ?? (runtime && mode ? `${providerName(runtime)} wird verwendet.` : "Hardwarestatus wird geladen.");

  useEffect(() => setOpen(false), [route]);

  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const anchor = trigger.current?.getBoundingClientRect();
      if (anchor) panel.current?.style.setProperty("--runtime-panel-top", `${anchor.bottom + 8}px`);
    };
    position();
    panel.current?.focus({ preventScroll: true });
    window.addEventListener("resize", position);
    return () => window.removeEventListener("resize", position);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !root.current?.contains(document.activeElement)) return;
      event.preventDefault();
      setOpen(false);
      trigger.current?.focus();
    };
    const navigation = () => setOpen(false);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("popstate", navigation);
    window.addEventListener("hashchange", navigation);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("popstate", navigation);
      window.removeEventListener("hashchange", navigation);
    };
  }, [open]);

  const startDiagnostic = async () => {
    setChecking(true);
    setDiagnostic(null);
    setDiagnosticError("");
    try {
      const response = await apiFetch(`${API_BASE}/runtime/gpu-diagnostic`, { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, "Der Hardwaretest ist gerade nicht verfügbar."));
      setDiagnostic(await response.json());
    } catch (error) {
      setDiagnosticError(error instanceof Error ? error.message : "Der Hardwaretest konnte nicht abgeschlossen werden.");
    } finally {
      setChecking(false);
    }
  };

  const outcome = REASONS[diagnostic?.provider_reason ?? ""];
  return <div className="runtime-status" ref={root}>
    <button type="button" ref={trigger} className={`compute-mode-badge compute-mode-badge--${mode ?? "unknown"}`}
      aria-label={`Hardwarestatus: ${label}`} aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
      <span className="compute-mode-badge__indicator" aria-hidden="true" />
      <span>{label}</span>
    </button>
    <section className="runtime-status__panel" id={panelId} ref={panel} tabIndex={-1} hidden={!open} aria-label="Hardwarestatus">
      <strong>Gesichtserkennung</strong>
      <p>{reason}</p>
      <Timings details={runtime} />
      {runtime?.host_platform !== "android" && <button type="button" className="secondary-button"
        disabled={checking || unavailable || state === "checking"} onClick={() => void startDiagnostic()}>
        {checking ? "Hardwaretest läuft …" : "Hardware prüfen"}
      </button>}
      {diagnostic && <div className="runtime-status__result">
        <p role={diagnostic.provider_state === "error" ? "alert" : "status"}>
          {outcome && outcome !== reason ? outcome : "Hardwaretest abgeschlossen."}
        </p>
        <Timings details={diagnostic} />
      </div>}
      {diagnosticError && <p role="alert">{diagnosticError}</p>}
    </section>
  </div>;
}
