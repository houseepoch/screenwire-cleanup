import { useState, useEffect, useRef } from 'react';

interface MousePosition {
  x: number;
  y: number;
  normalizedX: number;
  normalizedY: number;
}

export function useMousePosition(): MousePosition {
  const [position, setPosition] = useState<MousePosition>({
    x: 0,
    y: 0,
    normalizedX: 0,
    normalizedY: 0,
  });
  
  const tickingRef = useRef(false);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!tickingRef.current) {
        requestAnimationFrame(() => {
          const normalizedX = (e.clientX / window.innerWidth - 0.5) * 2;
          const normalizedY = (e.clientY / window.innerHeight - 0.5) * 2;
          
          setPosition({
            x: e.clientX,
            y: e.clientY,
            normalizedX,
            normalizedY,
          });
          
          // Update CSS custom properties
          document.documentElement.style.setProperty('--mouse-x', normalizedX.toString());
          document.documentElement.style.setProperty('--mouse-y', normalizedY.toString());
          
          tickingRef.current = false;
        });
        tickingRef.current = true;
      }
    };

    window.addEventListener('mousemove', handleMouseMove, { passive: true });
    
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
    };
  }, []);

  return position;
}
