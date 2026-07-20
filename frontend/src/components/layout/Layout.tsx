import React from "react";
import type { AppPage } from "../../App";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";

const Layout: React.FC<{
  page: AppPage;
  onChangePage: (p: AppPage) => void;
  onShowReleaseNotes?: () => void;
  onShowUpdate?: () => void;
  children: React.ReactNode;
}> = ({ page, onChangePage, onShowReleaseNotes, onShowUpdate, children }) => {
  return (
    <div className="app-shell">
      <Sidebar
        page={page}
        onChangePage={onChangePage}
        onShowReleaseNotes={onShowReleaseNotes}
        onShowUpdate={onShowUpdate}
      />

      <div className="app-main">
        <Topbar />
        <div className="app-viewport">{children}</div>
      </div>
    </div>
  );
};

export default Layout;
