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
  invitation?: React.ReactNode;
  children: React.ReactNode;
}> = ({ page, navigation, sharedLibrary, sharedLibraryReadError, onChangePage, onShowReleaseNotes, onShowUpdate,
  onShowHelp, onImportStateChange, topbarAction, invitation, children }) => {
  return (
    <div className="app-shell">
      <Sidebar
        page={page}
        onChangePage={onChangePage}
        onShowReleaseNotes={onShowReleaseNotes}
        onShowUpdate={onShowUpdate}
      />

      <div className="app-main">
        <Topbar route={`${navigation.id}:${window.location.hash}`}
          sharedLibrary={sharedLibrary} sharedLibraryReadError={sharedLibraryReadError} onShowHelp={onShowHelp}
          onImportStateChange={onImportStateChange} action={topbarAction} />
        <nav className="workspace-history" aria-label="Ansichtsverlauf">
          <button type="button" disabled={!navigation.canBack} onClick={() => navigation.step(-1)}
            title={navigation.backPage ? `Zurück zu „${labels[navigation.backPage]}“` : "Zurück"} aria-keyshortcuts="Alt+ArrowLeft">
            <span aria-hidden="true">←</span> Zurück
          </button>
          <button type="button" disabled={!navigation.canForward} onClick={() => navigation.step(1)}
            title={navigation.forwardPage ? `Vorwärts zu „${labels[navigation.forwardPage]}“` : "Vorwärts"} aria-keyshortcuts="Alt+ArrowRight">
            <span aria-hidden="true">→</span> Vorwärts
          </button>
          <button type="button" className="workspace-history__reset" onClick={navigation.reset}>Zur Ausgangsansicht</button>
        </nav>
        {navigation.notice && <p className="workspace-history__notice" role="status">{navigation.notice}</p>}
        {invitation}
        <div className="app-viewport">{children}</div>
      </div>
    </div>
  );
};

export default Layout;
