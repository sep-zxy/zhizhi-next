import { getCurrentWindow } from "@tauri-apps/api/window";
import "@fontsource-variable/inter";
import "./design-tokens.css";
import "./styles.css";
import { mountEditor } from "./editor";
import { mountPreview } from "./preview";
import { mountWidget } from "./widget";

const app = document.querySelector<HTMLDivElement>("#app");
if (!app) throw new Error("缺少 #app 根节点");

const fixture = new URLSearchParams(window.location.search).get("fixture");
const label = fixture ?? getCurrentWindow().label;
if (label === "widget") mountWidget(app);
else if (label === "preview") mountPreview(app);
else mountEditor(app);
