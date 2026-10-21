/** Diagnostic branch only: sparse, in-page recording; no transport or console writes. */
export function diagnosticMark(kind: string, value: unknown) {
  (window as Window & { __observerDiagnostic?: (kind: string, value: unknown) => void })
    .__observerDiagnostic?.(kind, value);
}
