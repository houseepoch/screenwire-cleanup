import { useCallback, useRef, useState } from 'react';

interface UseLongPressOptions {
  onLongPress: () => void;
  onClick?: () => void;
  ms?: number;
}

export function useLongPress({ onLongPress, onClick, ms = 500 }: UseLongPressOptions) {
  const [isPressing, setIsPressing] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isLongPressRef = useRef(false);

  const start = useCallback(() => {
    isLongPressRef.current = false;
    setIsPressing(true);
    timerRef.current = setTimeout(() => {
      isLongPressRef.current = true;
      onLongPress();
      setIsPressing(false);
    }, ms);
  }, [onLongPress, ms]);

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setIsPressing(false);
    if (!isLongPressRef.current && onClick) {
      onClick();
    }
  }, [onClick]);

  const cancel = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setIsPressing(false);
  }, []);

  return {
    handlers: {
      onMouseDown: start,
      onMouseUp: stop,
      onMouseLeave: cancel,
      onTouchStart: start,
      onTouchEnd: stop,
      onTouchMove: cancel,
    },
    isPressing,
  };
}
