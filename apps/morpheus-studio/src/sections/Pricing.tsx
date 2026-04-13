import { useEffect, useRef, useState } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { Check } from 'lucide-react';
import { pricingConfig } from '../config';

gsap.registerPlugin(ScrollTrigger);

export function Pricing() {
  const sectionRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const subtitleRef = useRef<HTMLParagraphElement>(null);
  const cardsRef = useRef<(HTMLDivElement | null)[]>([]);
  const [animatedPrices, setAnimatedPrices] = useState<number[]>([]);
  const triggersRef = useRef<ScrollTrigger[]>([]);

  if (!pricingConfig.title || pricingConfig.plans.length === 0) return null;

  const plans = pricingConfig.plans;

  useEffect(() => {
    setAnimatedPrices(new Array(plans.length).fill(0));

    const section = sectionRef.current;
    if (!section) return;

    const trigger = ScrollTrigger.create({
      trigger: section,
      start: 'top 80%',
      onEnter: () => {
        const tl = gsap.timeline();

        // Title fade up
        tl.fromTo(
          titleRef.current,
          { y: 40, opacity: 0 },
          { y: 0, opacity: 1, duration: 0.7, ease: 'expo.out' }
        );

        // Subtitle
        tl.fromTo(
          subtitleRef.current,
          { y: 20, opacity: 0 },
          { y: 0, opacity: 1, duration: 0.5, ease: 'power2.out' },
          '-=0.4'
        );

        // Cards 3D rotation entry
        cardsRef.current.forEach((card, i) => {
          if (card) {
            const rotateStart = i === 0 ? -45 : i === 2 ? 45 : 0;
            const translateX = i === 0 ? -100 : i === 2 ? 100 : 0;
            const translateY = i === 1 ? 80 : 0;
            const scaleStart = i === 1 ? 0.8 : 1;

            tl.fromTo(
              card,
              {
                rotateY: rotateStart,
                x: translateX,
                y: translateY,
                scale: scaleStart,
                opacity: 0,
              },
              {
                rotateY: 0,
                x: 0,
                y: 0,
                scale: 1,
                opacity: 1,
                duration: i === 1 ? 1.1 : 1,
                ease: i === 1 ? 'back.out(1.7)' : 'expo.out',
              },
              i === 1 ? '-=0.7' : `-=${0.8 - i * 0.1}`
            );

            // Red accent line for featured
            if (plans[i].featured) {
              const accentLine = card.querySelector('.accent-line');
              if (accentLine) {
                tl.fromTo(
                  accentLine,
                  { width: 0 },
                  { width: '100%', duration: 0.8, ease: 'expo.inOut' },
                  '-=0.5'
                );
              }
            }

            // Features stagger
            const features = card.querySelectorAll('.feature-item');
            tl.fromTo(
              features,
              { x: -10, opacity: 0 },
              {
                x: 0,
                opacity: 1,
                duration: 0.4,
                stagger: 0.05,
                ease: 'power2.out',
              },
              '-=0.6'
            );
          }
        });

        // Animate price counters
        plans.forEach((plan, i) => {
          const obj = { value: 0 };
          gsap.to(obj, {
            value: plan.price,
            duration: 1.2,
            delay: 0.5,
            ease: 'power2.out',
            onUpdate: () => {
              setAnimatedPrices((prev) => {
                const newPrices = [...prev];
                newPrices[i] = Math.round(obj.value);
                return newPrices;
              });
            },
          });
        });
      },
      once: true,
    });
    triggersRef.current.push(trigger);

    return () => {
      triggersRef.current.forEach((t) => t.kill());
      triggersRef.current = [];
    };
  }, []);

  const handleCardHover = (index: number, isEntering: boolean) => {
    const card = cardsRef.current[index];
    if (!card) return;

    if (isEntering) {
      gsap.to(card, {
        y: -10,
        z: 30,
        rotateY: 0,
        boxShadow: '0 30px 60px rgba(0,0,0,0.4)',
        duration: 0.4,
        ease: 'expo.out',
      });
    } else {
      gsap.to(card, {
        y: 0,
        z: 0,
        rotateY: 0,
        boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        duration: 0.4,
        ease: 'power2.out',
      });
    }
  };

  return (
    <section
      ref={sectionRef}
      id="pricing"
      className="relative py-32 px-8 lg:px-16 bg-black overflow-hidden"
    >
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="text-center mb-16">
          <h2
            ref={titleRef}
            className="text-h1 lg:text-display-xl text-white font-medium mb-4"
          >
            {pricingConfig.title}
          </h2>
          <p ref={subtitleRef} className="text-body-lg text-white/60">
            {pricingConfig.subtitle}
          </p>
        </div>

        {/* Pricing cards */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 lg:gap-8 items-stretch">
          {plans.map((plan, index) => (
            <div
              key={plan.id}
              ref={(el) => {
                cardsRef.current[index] = el;
              }}
              className={`relative p-8 lg:p-10 preserve-3d transition-colors duration-300 ${
                plan.featured
                  ? 'bg-white text-black'
                  : 'bg-dark-gray text-white hover:bg-dark-gray/80'
              }`}
              style={{
                transformStyle: 'preserve-3d',
                willChange: 'transform, box-shadow',
              }}
              onMouseEnter={() => handleCardHover(index, true)}
              onMouseLeave={() => handleCardHover(index, false)}
            >
              {/* Featured accent line */}
              {plan.featured && (
                <div
                  className="accent-line absolute top-0 left-0 h-1 bg-highlight"
                  style={{ willChange: 'width' }}
                />
              )}

              {/* Plan name */}
              <h3
                className={`text-h5 mb-6 ${
                  plan.featured ? 'text-black' : 'text-white/80'
                }`}
              >
                {plan.name}
              </h3>

              {/* Price */}
              <div className="mb-8">
                <span className="text-h4 lg:text-h3 font-medium tabular-nums">
                  ${(animatedPrices[index] || 0).toLocaleString()}.00
                </span>
                <span
                  className={`text-body ml-2 ${
                    plan.featured ? 'text-black/60' : 'text-white/60'
                  }`}
                >
                  / {plan.unit}
                </span>
              </div>

              {/* Features */}
              <ul className="space-y-4 mb-10">
                {plan.features.map((feature, i) => (
                  <li
                    key={i}
                    className={`feature-item flex items-center gap-3 text-body ${
                      plan.featured ? 'text-black/70' : 'text-white/70'
                    }`}
                  >
                    <Check
                      className={`w-5 h-5 flex-shrink-0 ${
                        plan.featured ? 'text-highlight' : 'text-highlight'
                      }`}
                    />
                    {feature}
                  </li>
                ))}
              </ul>

              {/* CTA Button */}
              <button
                className={`w-full py-4 text-body font-medium transition-all duration-200 ${
                  plan.featured
                    ? 'bg-black text-white hover:bg-highlight'
                    : 'border border-white/30 text-white hover:bg-white hover:text-black'
                }`}
              >
                {pricingConfig.ctaButtonText}
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
