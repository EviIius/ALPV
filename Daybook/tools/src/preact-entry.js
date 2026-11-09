// UI runtime: Preact + hooks + htm (JSX-like tagged templates, no build step
// needed for app code). Bundled into app/static/vendor/preact.js.
import { h } from 'preact';
import htm from 'htm';

export { h, render, Fragment, createContext, createRef, cloneElement, toChildArray } from 'preact';
export {
  useState, useEffect, useLayoutEffect, useMemo, useCallback, useRef,
  useReducer, useContext, useId, useErrorBoundary,
} from 'preact/hooks';

export const html = htm.bind(h);
