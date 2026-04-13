import { useEffect, useRef } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { aboutConfig } from '../config';

gsap.registerPlugin(ScrollTrigger);

export function About() {
  const sectionRef = useRef<HTMLElement>(null);
  const image1Ref = useRef<HTMLDivElement>(null);
  const image2Ref = useRef<HTMLDivElement>(null);
  const authorImageRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const textRef = useRef<HTMLParagraphElement>(null);
  const lineRef = useRef<HTMLDivElement>(null);
  const authorTextRef = useRef<HTMLDivElement>(null);
  const triggersRef = useRef<ScrollTrigger[]>([]);

  if (!aboutConfig.titleLine1) return null;

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;

    // Entry animations
    const trigger = ScrollTrigger.create({
      trigger: section,
      start: 'top 80%',
      onEnter: () => {
        const tl = gsap.timeline();

        // Image 1 clip reveal
        tl.fromTo(
          image1Ref.current,
          { clipPath: 'inset(100% 0 0 0)', scale: 1.1 },
          {
            clipPath: 'inset(0% 0 0 0)',
            scale: 1,
            duration: 1.2,
            ease: 'expo.out',
          }
        );

        // Image 2 clip reveal
        tl.fromTo(
          image2Ref.current,
          { clipPath: 'inset(0 100% 0 0)', scale: 1.05 },
          {
            clipPath: 'inset(0 0% 0 0)',
            scale: 1,
            duration: 1.1,
            ease: 'expo.out',
          },
          '-=0.9'
        );

        // Title lines reveal
        if (titleRef.current) {
          const lines = titleRef.current.querySelectorAll('.title-line');
          tl.fromTo(
            lines,
            { y: 40, opacity: 0 },
            {
              y: 0,
              opacity: 1,
              duration: 0.8,
              stagger: 0.2,
              ease: 'back.out(1.7)',
            },
            '-=0.8'
          );
        }

        // Text fade up
        tl.fromTo(
          textRef.current,
          { y: 30, opacity: 0 },
          { y: 0, opacity: 1, duration: 0.7, ease: 'power2.out' },
          '-=0.4'
        );

        // Red accent line
        tl.fromTo(
          lineRef.current,
          { width: 0 },
          { width: '120%', duration: 1, ease: 'expo.inOut' },
          '-=0.6'
        );

        // Author image
        tl.fromTo(
          authorImageRef.current,
          { scale: 0.9, rotate: -3, opacity: 0 },
          {
            scale: 1,
            rotate: 0,
            opacity: 1,
            duration: 0.9,
            ease: 'elastic.out(1, 0.5)',
          },
          '-=0.7'
        );

        // Author text words
        if (authorTextRef.current) {
          const words = authorTextRef.current.querySelectorAll('.word');
          tl.fromTo(
            words,
            { y: 15, opacity: 0 },
            {
              y: 0,
              opacity: 1,
              duration: 0.6,
              stagger: 0.05,
              ease: 'power2.out',
            },
            '-=0.5'
          );
        }
      },
      once: true,
    });
    triggersRef.current.push(trigger);

    // Parallax on scroll
    const parallaxTrigger = ScrollTrigger.create({
      trigger: section,
      start: 'top bottom',
      end: 'bottom top',
      scrub: 1,
      onUpdate: (self) => {
        if (image1Ref.current) {
          gsap.set(image1Ref.current, {
            y: 50 - self.progress * 100,
          });
        }
        if (image2Ref.current) {
          gsap.set(image2Ref.current, {
            y: -30 + self.progress * 60,
          });
        }
        if (authorImageRef.current) {
          gsap.set(authorImageRef.current, {
            y: 30 - self.progress * 60,
          });
        }
      },
    });
    triggersRef.current.push(parallaxTrigger);

    return () => {
      triggersRef.current.forEach((t) => t.kill());
      triggersRef.current = [];
    };
  }, []);

  const authorTextChars = aboutConfig.authorBio.split('');

  return (
    <section
      ref={sectionRef}
      id="about"
      className="relative py-32 px-8 lg:px-16 overflow-hidden"
      style={{ transform: 'rotate(0deg)' }}
    >
      {/* Background gradient */}
      <div className="absolute inset-0 bg-gradient-to-b from-black via-[#0a0a0a] to-black z-0" />

      <div className="relative z-10 max-w-7xl mx-auto">
        {/* Section layout */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-12">
          {/* Left column - Images */}
          <div className="lg:col-span-5 relative">
            {/* Image 1 */}
            <div
              ref={image1Ref}
              className="relative w-full aspect-[2/3] overflow-hidden group cursor-pointer"
              style={{ willChange: 'clip-path, transform' }}
            >
              <img
                src={aboutConfig.image1}
                alt={aboutConfig.image1Alt}
                className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
              />
              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors duration-300" />
            </div>

            {/* Image 2 - overlapping */}
            <div
              ref={image2Ref}
              className="relative w-3/4 aspect-[2/3] -mt-32 ml-auto mr-4 overflow-hidden group cursor-pointer z-10"
              style={{ willChange: 'clip-path, transform' }}
            >
              <img
                src={aboutConfig.image2}
                alt={aboutConfig.image2Alt}
                className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
              />
              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors duration-300" />
            </div>
          </div>

          {/* Right column - Content */}
          <div className="lg:col-span-7 flex flex-col justify-center">
            {/* Section title */}
            <h2
              ref={titleRef}
              className="text-h3 lg:text-h2 text-white font-normal leading-tight mb-8"
            >
              <span className="title-line block">
                {aboutConfig.titleLine1}
              </span>
              <span className="title-line block">
                {aboutConfig.titleLine2}
              </span>
            </h2>

            {/* Red accent line */}
            <div
              ref={lineRef}
              className="h-[2px] bg-highlight mb-8 animate-pulse-glow"
              style={{ willChange: 'width' }}
            />

            {/* About text */}
            <p
              ref={textRef}
              className="text-body-lg text-white/70 leading-relaxed mb-12"
            >
              {aboutConfig.description}
            </p>

            {/* Author section */}
            <div className="flex items-start gap-6">
              {/* Author image */}
              <div
                ref={authorImageRef}
                className="w-24 h-24 flex-shrink-0 overflow-hidden rounded-full border-2 border-highlight/30"
                style={{ willChange: 'transform, opacity' }}
              >
                <img
                  src={aboutConfig.authorImage}
                  alt={aboutConfig.authorName}
                  className="w-full h-full object-cover"
                />
              </div>

              {/* Author text */}
              <div ref={authorTextRef} className="text-body text-white/60">
                {authorTextChars.map((char, i) => (
                  <span key={i} className="word inline">
                    {char}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
