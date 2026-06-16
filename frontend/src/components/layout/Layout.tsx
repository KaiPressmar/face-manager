import React from "react";
import type { AppPage } from "../../App";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";

const Layout: React.FC<{
  page: AppPage;
  onChangePage: (p: AppPage) => void;
  children: React.ReactNode;
}> = ({ page, onChangePage, children }) => {
  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <Sidebar page={page} onChangePage={onChangePage} />

      <div style={{ flex: 1, position: "relative" }}>
        <Topbar />

        <div className="page-content">
          {children}
        </div>
      </div>
    </div>
  );
};

export default Layout;
