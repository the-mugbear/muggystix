import { useCallback, useEffect, useRef } from 'react';

/**
 * Is this component still on screen?
 *
 * A state update after unmount is already a no-op, so this is not for those.
 * It is for what outlives the component: a toast that speaks about "this"
 * record, a window event, a follow-up request — things a save that answers
 * after the reader moved on must not do.
 */
export const useIsMounted = (): (() => boolean) => {
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  return useCallback(() => mounted.current, []);
};

export default useIsMounted;
