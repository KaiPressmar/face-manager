import React from "react";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import ImportProgress from "../import/ImportProgress";

const Layout: React.FC<{
  page: "people" | "clusters";
  onChangePage: (p: "people" | "clusters") => void;
  children: React.ReactNode;
}> = ({ page, onChangePage, children }) => {
  return (
    <div style={{ display: "flex", height: "100vh" }}>
      <Sidebar page={page} onChangePage={onChangePage} />

      <div style={{ flex: 1, position: "relative" }}>
        {/* 🔥 Fortschrittsanzeige für Ordner-Import */}
        <ImportProgress />

        <Topbar />

        <div className="page-content">
          {children}
        </div>
      </div>
    </div>
  );
};

export default Layout;
