import React from "react";
import type { AppPage } from "../../App";
import type { SharedLibraryState } from "../../utils/api";
import Sidebar from "./Sidebar";
import Topbar, { type TopbarAction } from "./Topbar";
import "./help-entry.css";

const Layout: React.FC<{
  page: AppPage;
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
}> = ({ page, sharedLibrary, sharedLibraryReadError, onChangePage, onShowReleaseNotes, onShowUpdate,
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
        <Topbar route={`${page}:${window.location.hash}`} sharedLibrary={sharedLibrary} sharedLibraryReadError={sharedLibraryReadError} onShowHelp={onShowHelp}
          onImportStateChange={onImportStateChange} action={topbarAction} />
        {invitation}
        <div className="app-viewport">{children}</div>
      </div>
    </div>
  );
};

export default Layout;
