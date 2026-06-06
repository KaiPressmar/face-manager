import React, { useState } from "react";
import Layout from "./components/layout/Layout";
import PeoplePage from "./components/people/PeoplePage";
import ClusterPage from "./components/clusters/ClusterPage";

const App: React.FC = () => {
  const [page, setPage] = useState<"people" | "clusters">("people");

  return (
    <Layout page={page} onChangePage={setPage}>
      {page === "people" && <PeoplePage />}
      {page === "clusters" && <ClusterPage />}
    </Layout>
  );
};

export default App;
