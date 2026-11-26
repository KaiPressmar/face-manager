import type { AppPage } from "../../utils/navigation";

interface WorkspaceDestination {
  page: AppPage;
  icon: string;
  label: string;
  hint: string;
  keywords: string;
}

export const PRIMARY_ITEMS: WorkspaceDestination[] = [
  { page: "people", icon: "▦", label: "Bilder", hint: "Fotos und Alben", keywords: "bibliothek galerie ordner" },
  { page: "review", icon: "◉", label: "Personen", hint: "Gesichter benennen", keywords: "vorschläge prüfen zuordnung archiv" },
  { page: "map", icon: "◎", label: "Karte", hint: "Aufnahmeorte und Reisen", keywords: "satellit verlauf reisealbum fotobuch" },
];

export const SECONDARY_ITEMS: WorkspaceDestination[] = [
  { page: "renaming", icon: "Aa", label: "Dateinamen", hint: "Personennamen ergänzen", keywords: "umbenennen dateien" },
  { page: "settings", icon: "⚙", label: "Einstellungen", hint: "Face Manager verwalten", keywords: "sicherung backup darstellung updates wartung" },
];
