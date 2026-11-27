import { createContext, useContext } from "react";

export interface EditorFocusContextValue {
  isEditorFocusMode: boolean;
  isSidebarDrawerOpen: boolean;
  toggleSidebarDrawer: () => void;
  closeSidebarDrawer: () => void;
  setEditorFocusMounted: (active: boolean) => void;
}

export const EditorFocusContext = createContext<EditorFocusContextValue>({
  isEditorFocusMode: false,
  isSidebarDrawerOpen: false,
  toggleSidebarDrawer: () => {},
  closeSidebarDrawer: () => {},
  setEditorFocusMounted: () => {},
});

export function useEditorFocus(): EditorFocusContextValue {
  return useContext(EditorFocusContext);
}

/**
 * Matches active site editor routes (/publisher/websites/:id and /admin/websites/:id),
 * excluding the websites list routes (/publisher/websites and /admin/websites).
 */
export function isWebsiteEditorPath(pathname: string): boolean {
  return /^\/(?:publisher|admin)\/websites\/[^/]+\/?$/.test(pathname);
}
