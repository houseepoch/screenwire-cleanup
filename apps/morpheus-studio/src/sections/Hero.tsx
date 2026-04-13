import { useEffect, useRef, useState } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { heroConfig } from '../config';

gsap.registerPlugin(ScrollTrigger);

export function Hero() {
  const sectionRef = useRef<HTMLElement>(null);
  const imageRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const subtitleRef = useRef<HTMLParagraphElement>(null);
  const servicesRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<HTMLDivElement>(null);
  const copyrightRef = useRef<HTMLDivElement>(null);
  const [, setLoaded] = useState(false);
  const triggersRef = useRef<ScrollTrigger[]>([]);

  if (!heroConfig.title) return null;

  useEffect(() => {
    // Entry animation on load
    const tl = gsap.timeline({ delay: 0.2 });

    // Image scale + fade
    tl.fromTo(
      imageRef.current,
      { scale: 1.1, opacity: 0 },
      { scale: 1, opacity: 1, duration: 1.8, ease: 'expo.out' }
    );

    // Title characters animation
    if (titleRef.current) {
      const chars = titleRef.current.querySelectorAll('.char');
      tl.fromTo(
        chars,
        { rotateY: -90, y: 60, opacity: 0 },
        {
          rotateY: 0,
          y: 0,
          opacity: 1,
          duration: 0.9,
          stagger: 0.1,
          ease: 'back.out(1.7)',
        },
        '-=1.4'
      );
    }

    // Subtitle blur reveal
    tl.fromTo(
      subtitleRef.current,
      { filter: 'blur(20px)', opacity: 0 },
      { filter: 'blur(0px)', opacity: 1, duration: 0.8, ease: 'power2.out' },
      '-=0.6'
    );

    // Services slide in
    tl.fromTo(
      servicesRef.current,
      { x: -50, opacity: 0 },
      { x: 0, opacity: 1, duration: 0.7, ease: 'expo.out' },
      '-=0.4'
    );

    // Line grow
    tl.fromTo(
      lineRef.current,
      { height: 0 },
      { height: 200, duration: 1.5, ease: 'expo.inOut' },
      '-=0.8'
    );

    // Copyright fade
    tl.fromTo(
      copyrightRef.current,
      { y: 20, opacity: 0 },
      { y: 0, opacity: 1, duration: 0.5, ease: 'power2.out' },
      '-=1'
    );

    setLoaded(true);

    // Scroll effects
    const trigger1 = ScrollTrigger.create({
      trigger: sectionRef.current,
      start: 'top top',
      end: '50% top',
      scrub: 1,
      onUpdate: (self) => {
        if (imageRef.current) {
          gsap.set(imageRef.current, {
            y: `${self.progress * 45}%`,
            opacity: 1 - self.progress * 0.65,
          });
        }
      },
    });
    triggersRef.current.push(trigger1);

    const trigger2 = ScrollTrigger.create({
      trigger: sectionRef.current,
      start: 'top top',
      end: '30% top',
      scrub: 1,
      onUpdate: (self) => {
        if (titleRef.current) {
          gsap.set(titleRef.current, {
            rotateX: -15 * self.progress,
            z: -100 * self.progress,
          });
        }
      },
    });
    triggersRef.current.push(trigger2);

    const trigger3 = ScrollTrigger.create({
      trigger: sectionRef.current,
      start: '10% top',
      end: '40% top',
      scrub: 1,
      onUpdate: (self) => {
        if (subtitleRef.current) {
          gsap.set(subtitleRef.current, {
            opacity: 1 - self.progress,
            y: -30 * self.progress,
          });
        }
      },
    });
    triggersRef.current.push(trigger3);

    return () => {
      tl.kill();
      triggersRef.current.forEach((t) => t.kill());
      triggersRef.current = [];
    };
  }, []);

  const titleChars = heroConfig.title.split('');

  return (
    <section
      ref={sectionRef}
      id="hero"
      className="relative h-screen w-full overflow-hidden perspective-container"
      style={{ perspective: '1200px' }}
    >
      {/* Vignette overlay */}
      <div
        className="absolute inset-0 z-10 pointer-events-none"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.6) 100%)',
        }}
      />

      {/* Main background image */}
      <div
        ref={imageRef}
        className="absolute inset-0 z-0"
        style={{
          willChange: 'transform, opacity',
        }}
      >
        <img
          src={heroConfig.backgroundImage}
          alt="Hero"
          className="w-full h-full object-cover"
          style={{ filter: 'brightness(0.9)' }}
        />
        {/* Chromatic aberration effect layers */}
        <div
          className="absolute inset-0 mix-blend-multiply opacity-50"
          style={{
            backgroundImage: `url(${heroConfig.backgroundImage})`,
            backgroundSize: 'cover',
            backgroundPosition: 'center',
            transform: 'translateX(-2px)',
            filter: 'url(#red-channel)',
          }}
        />
      </div>

      {/* Content container */}
      <div
        className="relative z-20 h-full w-full flex flex-col justify-center items-center px-8"
        style={{ transformStyle: 'preserve-3d' }}
      >
        {/* Main title */}
        <h1
          ref={titleRef}
          className="text-[120px] md:text-[168px] font-medium text-white tracking-tight mb-4 preserve-3d"
          style={{
            textShadow: '0 0 80px rgba(234, 0, 0, 0.3)',
            willChange: 'transform',
          }}
        >
          {titleChars.map((char, i) => (
            <span
              key={i}
              className="char inline-block"
              style={{
                transform: `translateY(${(i % 2 === 0 ? -1 : 1) * 8}px)`,
              }}
            >
              {char}
            </span>
          ))}
        </h1>

        {/* Subtitle */}
        <p
          ref={subtitleRef}
          className="text-h3 font-extralight text-white/80 tracking-widest"
          style={{ willChange: 'filter, opacity' }}
        >
          {heroConfig.subtitle}
        </p>

        {/* Decorative accent line */}
        <div
          className="absolute left-1/2 bottom-32 w-px bg-highlight z-30"
          ref={lineRef}
          style={{
            transform: 'translateX(-50%)',
            willChange: 'height',
          }}
        />
      </div>

      {/* Services label - vertical left */}
      <div
        ref={servicesRef}
        className="absolute left-8 bottom-32 z-30 flex flex-col items-center gap-4"
        style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
      >
        <span className="text-body-sm text-white/60 tracking-widest">
          {heroConfig.servicesLabel}
        </span>
      </div>

      {/* Copyright - bottom right */}
      <div
        ref={copyrightRef}
        className="absolute right-8 bottom-8 z-30"
      >
        <span className="text-body-sm text-white/40">{heroConfig.copyright}</span>
      </div>

      {/* SVG filters for chromatic aberration */}
      <svg className="absolute w-0 h-0">
        <defs>
          <filter id="red-channel">
            <feColorMatrix
              type="matrix"
              values="1 0 0 0 0
                      0 0 0 0 0
                      0 0 0 0 0
                      0 0 0 1 0"
            />
          </filter>
          <filter id="blue-channel">
            <feColorMatrix
              type="matrix"
              values="0 0 0 0 0
                      0 0 0 0 0
                      0 0 1 0 0
                      0 0 0 1 0"
            />
          </filter>
        </defs>
      </svg>
    </section>
  );
}
