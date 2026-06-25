import React, { Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./styles/globals.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <Suspense fallback={null}>
        <App />
      </Suspense>
    </BrowserRouter>
  </React.StrictMode>,
);
