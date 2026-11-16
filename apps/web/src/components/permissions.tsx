"use client";
import { createContext, ReactNode, useContext } from "react";
const PermissionContext = createContext<{
  permissions: readonly string[];
  allowedDistricts: string[] | null;
}>({ permissions: [], allowedDistricts: [] });
export function PermissionProvider({
  permissions,
  allowedDistricts,
  children,
}: {
  permissions: readonly string[];
  allowedDistricts: string[] | null;
  children: ReactNode;
}) {
  return (
    <PermissionContext.Provider value={{ permissions, allowedDistricts }}>
      {children}
    </PermissionContext.Provider>
  );
}
export function useCapability(code: string) {
  return useContext(PermissionContext).permissions.includes(code);
}
export function useDistrictScope() {
  return useContext(PermissionContext).allowedDistricts;
}
export function useGlobalScope() {
  return useDistrictScope() === null;
}
