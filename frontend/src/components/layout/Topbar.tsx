import React from "react";

const Topbar = () => {
  return (
    <header
      style={{
        height: 64,
        background: "#141418",
        borderBottom: "1px solid #1f1f22",
        display: "flex",
        alignItems: "center",
        paddingLeft: 24,
        fontSize: 20,
      }}
    >
      Face Manager
    </header>
  );
};

export default Topbar;
