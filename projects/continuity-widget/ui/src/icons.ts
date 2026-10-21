import {
  AlertCircle, Archive, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  Circle, CircleHelp, Clock3, Columns3, ExternalLink, Filter, FolderOpen, Gauge,
  GripVertical, LayoutList, Maximize2, Menu, MoreHorizontal, PanelLeftClose,
  PanelTop, Pause, Pin, Plus, RefreshCw, Search, Settings, Sparkles, Square,
  Target, X, XCircle, createIcons,
} from "lucide";

const icons = {
  AlertCircle, Archive, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  Circle, CircleHelp, Clock3, Columns3, ExternalLink, Filter, FolderOpen, Gauge,
  GripVertical, LayoutList, Maximize2, Menu, MoreHorizontal, PanelLeftClose,
  PanelTop, Pause, Pin, Plus, RefreshCw, Search, Settings, Sparkles, Square,
  Target, X, XCircle,
};

export function hydrateIcons(): void { createIcons({ icons }); }
