import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { UiPreferencesProvider } from "./hooks/useUiPreferences";
import { ThemeProvider } from "./theme/ThemeContext";
import { applyTheme, readCachedTheme } from "./utils/theme";

import "./styles/global.css";
import "./styles/neon.css";
import "./styles/hologram.css";
import "./styles/responsive.css";
import "./styles/workflow.css";

// Apply any explicitly pinned theme before the first paint so a Light/Dark
// choice never flashes the wrong theme. "system" resolves via CSS media query.
applyTheme(readCachedTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <UiPreferencesProvider>
        <App />
      </UiPreferencesProvider>
    </ThemeProvider>
  </React.StrictMode>
);
