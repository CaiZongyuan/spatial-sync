import { createContext } from 'react';

/** Live viewports report their actual source version; recorded views never do. */
export const PlatformRuntimeContext = createContext<(owner: string, newtonVersion?: string) => void>(() => {});
