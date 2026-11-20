"use client";
import { createContext, useContext, useState, type ReactNode } from "react";
const Context = createContext<{
  view: string;
  setView: (view: string) => void;
  sidebar: boolean;
} | null>(null);
export function BusinessViewProvider({
  view,
  setView,
  sidebar,
  children,
}: {
  view: string;
  setView: (view: string) => void;
  sidebar: boolean;
  children: ReactNode;
}) {
  return (
    <Context.Provider value={{ view, setView, sidebar }}>
      {children}
    </Context.Provider>
  );
}
export function useBusinessView(
  initial: string,
): [string, (value: string) => void] {
  const context = useContext(Context);
  const [local, setLocal] = useState(initial);
  return context
    ? [context.view || initial, context.setView]
    : [local, setLocal];
}
export function useSidebarViews() {
  return useContext(Context)?.sidebar ?? false;
}
