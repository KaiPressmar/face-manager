import type { AppPage } from "../../utils/navigation";

interface WorkspaceDestination {
  page: AppPage;
  icon: string;
  label: string;
  hint: string;
}

export const PRIMARY_ITEMS: WorkspaceDestination[] = [
  { page: "people", icon: "▦", label: "Bilder", hint: "Fotos und Alben" },
  { page: "review", icon: "◉", label: "Personen", hint: "Gesichter benennen" },
  { page: "map", icon: "◎", label: "Karte", hint: "Aufnahmeorte und Reisen" },
];

export const SECONDARY_ITEMS: WorkspaceDestination[] = [
  { page: "renaming", icon: "Aa", label: "Dateinamen", hint: "Personennamen ergänzen" },
  { page: "settings", icon: "⚙", label: "Einstellungen", hint: "Face Manager verwalten" },
];
