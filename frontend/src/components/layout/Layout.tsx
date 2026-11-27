import React from "react";
import type { AppPage } from "../../App";
import type { SharedLibraryState } from "../../utils/api";
import Sidebar from "./Sidebar";
import Topbar, { type TopbarAction } from "./Topbar";
import { PRIMARY_ITEMS, SECONDARY_ITEMS } from "./workspaceDestinations";
import "./help-entry.css";
import "./workspace-history.css";

const labels = Object.fromEntries([...PRIMARY_ITEMS, ...SECONDARY_ITEMS].map((item) => [item.page, item.label]));

interface NavigationControls {
  id: string;
  canBack: boolean;
  canForward: boolean;
  backPage?: AppPage;
  forwardPage?: AppPage;
  step: (direction: -1 | 1) => boolean;
  reset: () => void;
  notice?: string;
}

const Layout: React.FC<{
  page: AppPage;
  navigation: NavigationControls;
  sharedLibrary?: SharedLibraryState | null;
  sharedLibraryReadError?: string | null;
  onChangePage: (p: AppPage) => void;
  onShowReleaseNotes?: () => void;
  onShowUpdate?: () => void;
  onShowHelp: () => void;
  onImportStateChange: (open: boolean) => void;
  topbarAction?: TopbarAction;
  preferenceNotice?: React.ReactNode;
  children: React.ReactNode;
}> = ({ page, navigation, sharedLibrary, sharedLibraryReadError, onChangePage, onShowReleaseNotes, onShowUpdate,
  onShowHelp, onImportStateChange, topbarAction, preferenceNotice, children }) => {
  return (
    <div className="app-shell">
      <a className="workspace-skip-link" href="#app-workspace" onClick={(event) => {
        event.preventDefault();
        document.getElementById("app-workspace")?.focus({ preventScroll: true });
      }}>Zum Hauptinhalt</a>
      <Sidebar
        page={page}
        onChangePage={onChangePage}
        onShowReleaseNotes={onShowReleaseNotes}
        onShowUpdate={onShowUpdate}
      />

      <div className="app-main">
        <div className="workspace-header">
          <nav className="workspace-history" aria-label="Ansichtsverlauf">
            <button type="button" aria-label="Zurück" disabled={!navigation.canBack} onClick={() => navigation.step(-1)}
              title={navigation.backPage ? `Zurück zu „${labels[navigation.backPage]}“` : "Zurück"} aria-keyshortcuts="Alt+ArrowLeft">
              <span aria-hidden="true">←</span><span className="workspace-history__direction">Zurück</span>
            </button>
            <button type="button" aria-label="Vorwärts" disabled={!navigation.canForward} onClick={() => navigation.step(1)}
              title={navigation.forwardPage ? `Vorwärts zu „${labels[navigation.forwardPage]}“` : "Vorwärts"} aria-keyshortcuts="Alt+ArrowRight">
              <span aria-hidden="true">→</span><span className="workspace-history__direction">Vorwärts</span>
            </button>
            <button type="button" className="workspace-history__reset" onClick={navigation.reset}>Zur Ausgangsansicht</button>
          </nav>
          <Topbar route={`${navigation.id}:${window.location.hash}`}
            sharedLibrary={sharedLibrary} sharedLibraryReadError={sharedLibraryReadError} onShowHelp={onShowHelp}
            onImportStateChange={onImportStateChange} action={topbarAction} />
        </div>
        {navigation.notice && <p className="workspace-history__notice" role="status">{navigation.notice}</p>}
        {preferenceNotice}
        <main id="app-workspace" className="app-viewport" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
};

export default Layout;
