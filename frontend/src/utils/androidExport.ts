import { API_BASE, apiFetch } from "./api";

type AndroidSaveResult = { status: "saved" | "cancelled" } | { status: "error"; message: string };

const saveErrors: Record<string, string> = {
  OCCUPIED: "Es wird bereits eine Datei gespeichert. Versuche es danach erneut.",
  PICKER_ERROR: "Die Dateiauswahl konnte nicht geöffnet werden.",
  SNAPSHOT_ERROR: "Die Datei konnte nicht zum Speichern vorbereitet werden.",
  WRITE_ERROR: "Die Datei konnte nicht gespeichert werden. Wähle einen anderen Speicherort.",
  PARTIAL_FILE: "Die Datei ist unvollständig. Lösche sie am gewählten Speicherort und versuche es erneut.",
};

export function isAndroidExportAvailable() {
  return Boolean(window.AndroidExport?.requestStagedFileExport && window.AndroidExport.getStagedFileExportStatus);
}

export async function saveAndroidExport(filename: string, blob: Blob): Promise<AndroidSaveResult> {
  const bridge = window.AndroidExport;
  if (!bridge?.requestStagedFileExport || !bridge.getStagedFileExportStatus) {
    return { status: "error", message: "Die Dateiauswahl ist auf diesem Gerät nicht verfügbar." };
  }
  const endpoint = `${API_BASE}/android/exports`;
  const response = await apiFetch(endpoint, { method: "POST", body: blob });
  if (!response.ok) {
    return { status: "error", message: "Die Datei konnte nicht zum Speichern vorbereitet werden." };
  }
  const { token } = await response.json() as { token: string };
  try {
    return await new Promise<AndroidSaveResult>((resolve) => {
      const finish = (result: AndroidSaveResult) => {
        window.removeEventListener("face-manager:android-export", readStatus);
        resolve(result);
      };
      const readStatus = (event?: Event) => {
        try {
          const status = bridge.getStagedFileExportStatus?.(token) ?? "IDLE";
          if (status === "SAVED") finish({ status: "saved" });
          else if (status === "CANCELLED") finish({ status: "cancelled" });
          else if (saveErrors[status]) finish({ status: "error", message: saveErrors[status] });
          else return;
          event?.preventDefault();
        } catch {
          finish({ status: "error", message: "Der Speicherstatus konnte nicht gelesen werden." });
        }
      };
      window.addEventListener("face-manager:android-export", readStatus);
      try {
        bridge.requestStagedFileExport?.(token, filename);
        readStatus();
      } catch {
        finish({ status: "error", message: "Die Dateiauswahl konnte nicht geöffnet werden." });
      }
    });
  } finally {
    // A rejected picker can leave the staged upload unclaimed.
    void apiFetch(`${endpoint}/${encodeURIComponent(token)}`, { method: "DELETE" }).catch(() => {});
  }
}
