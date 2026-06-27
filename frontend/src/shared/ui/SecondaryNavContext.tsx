import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { CollapsibleNavItem } from './CollapsibleIconNav';

export type SecondaryNavConfig = {
  title: string;
  items: CollapsibleNavItem[];
  footer?: ReactNode;
} | null;

type SecondaryNavContextValue = {
  config: SecondaryNavConfig;
  setConfig: (config: SecondaryNavConfig) => void;
};

const SecondaryNavContext = createContext<SecondaryNavContextValue | null>(null);

export function SecondaryNavProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<SecondaryNavConfig>(null);
  const value = useMemo(() => ({ config, setConfig }), [config]);
  return (
    <SecondaryNavContext.Provider value={value}>
      {children}
    </SecondaryNavContext.Provider>
  );
}

export function useSecondaryNavContext() {
  return useContext(SecondaryNavContext);
}

/** Register secondary nav in AppShell sidebar (replaces chat list). Clears on unmount. */
export function useRegisterSecondaryNav(config: SecondaryNavConfig) {
  const ctx = useSecondaryNavContext();
  const configRef = useRef(config);
  configRef.current = config;

  useEffect(() => {
    if (!ctx) return;
    ctx.setConfig(configRef.current);
    return () => ctx.setConfig(null);
  }, [ctx]);

  useEffect(() => {
    ctx?.setConfig(configRef.current);
  });
}
