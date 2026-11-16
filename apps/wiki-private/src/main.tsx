import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { WikiApp } from "./WikiApp";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <WikiApp />
  </StrictMode>,
);
