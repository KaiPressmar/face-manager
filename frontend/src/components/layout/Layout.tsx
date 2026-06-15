import React from "react";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";

const Layout: React.FC<{
  page: "people" | "clusters" | "renaming" | "settings";
  onChangePage: (p: "people" | "clusters" | "renaming" | "settings") => void;
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
