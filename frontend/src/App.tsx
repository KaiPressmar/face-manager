import React, { useState } from "react";
import Layout from "./components/layout/Layout";
import PeoplePage from "./components/people/PeoplePage";
import ClusterPage from "./components/clusters/ClusterPage";
import ImageRenamePage from "./components/renaming/ImageRenamePage";
import SettingsPage from "./components/settings/SettingsPage";

export type AppPage = "people" | "clusters" | "renaming" | "settings";

export interface ClusterNavigationTarget {
  clusterId: number;
  token: number;
}

const App: React.FC = () => {
  const [page, setPage] = useState<AppPage>("people");
  const [clusterNavigationTarget, setClusterNavigationTarget] =
    useState<ClusterNavigationTarget | null>(null);

  const handleNavigateToCluster = (clusterId: number) => {
    setClusterNavigationTarget({
      clusterId,
      token: Date.now(),
    });
    setPage("clusters");
  };

  return (
    <Layout page={page} onChangePage={setPage}>
      {page === "people" && (
        <PeoplePage onNavigateToCluster={handleNavigateToCluster} />
      )}
      {page === "clusters" && (
        <ClusterPage navigationTarget={clusterNavigationTarget} />
      )}
      {page === "renaming" && <ImageRenamePage />}
      {page === "settings" && <SettingsPage />}
    </Layout>
  );
};

export default App;
