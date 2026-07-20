import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ThemeProvider } from "./theme/ThemeContext";
import { applyTheme, readCachedTheme } from "./utils/theme";

import "./styles/global.css";
import "./styles/neon.css";
import "./styles/hologram.css";

// Apply any explicitly pinned theme before the first paint so a Light/Dark
// choice never flashes the wrong theme. "system" resolves via CSS media query.
applyTheme(readCachedTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>
);
