import React, { useState } from "react";
import Layout from "./components/layout/Layout";
import PeoplePage from "./components/people/PeoplePage";
import ClusterPage from "./components/clusters/ClusterPage";
import ImageRenamePage from "./components/renaming/ImageRenamePage";
import SettingsPage from "./components/settings/SettingsPage";

const App: React.FC = () => {
  const [page, setPage] = useState<
    "people" | "clusters" | "renaming" | "settings"
  >("people");

  return (
    <Layout page={page} onChangePage={setPage}>
      {page === "people" && <PeoplePage />}
      {page === "clusters" && <ClusterPage />}
      {page === "renaming" && <ImageRenamePage />}
      {page === "settings" && <SettingsPage />}
    </Layout>
  );
};

export default App;
