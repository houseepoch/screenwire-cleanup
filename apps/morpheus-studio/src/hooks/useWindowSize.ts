import { useState, useEffect } from 'react';

interface WindowSize {
  width: number;
  height: number;
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
}

export function useWindowSize(): WindowSize {
  const [windowSize, setWindowSize] = useState<WindowSize>({
    width: typeof window !== 'undefined' ? window.innerWidth : 1200,
    height: typeof window !== 'undefined' ? window.innerHeight : 800,
    isMobile: false,
    isTablet: false,
    isDesktop: true,
  });

  useEffect(() => {
    function handleResize() {
      const width = window.innerWidth;
      const height = window.innerHeight;
      
      setWindowSize({
        width,
        height,
        isMobile: width < 768,
        isTablet: width >= 768 && width < 1024,
        isDesktop: width >= 1024,
      });
    }

    handleResize();
    window.addEventListener('resize', handleResize);
    
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return windowSize;
}

// Modal constraints based on viewport
export function getModalConstraints(windowHeight: number) {
  const maxHeight = Math.min(windowHeight * 0.9, 700);
  const contentMaxHeight = maxHeight - 140; // Account for header/footer
  
  return {
    maxHeight,
    contentMaxHeight,
    padding: windowHeight < 600 ? '16px' : '24px',
  };
}

// Responsive grid columns based on width
export function getGridColumns(width: number): number {
  if (width < 480) return 1;
  if (width < 768) return 2;
  if (width < 1200) return 3;
  return 4;
}
