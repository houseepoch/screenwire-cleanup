import { useEffect, useRef, useState } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { Plus } from 'lucide-react';
import { faqConfig } from '../config';

gsap.registerPlugin(ScrollTrigger);

export function FAQ() {
  const sectionRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const itemsRef = useRef<(HTMLDivElement | null)[]>([]);
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const triggersRef = useRef<ScrollTrigger[]>([]);

  if (!faqConfig.title || faqConfig.faqs.length === 0) return null;

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;

    const trigger = ScrollTrigger.create({
      trigger: section,
      start: 'top 80%',
      onEnter: () => {
        const tl = gsap.timeline();

        // Title scale in
        tl.fromTo(
          titleRef.current,
          { scale: 0.9, opacity: 0 },
          { scale: 1, opacity: 1, duration: 0.7, ease: 'expo.out' }
        );

        // FAQ items stagger from alternating sides
        itemsRef.current.forEach((item, i) => {
          if (item) {
            const fromX = i % 2 === 0 ? -60 : 60;
            tl.fromTo(
              item,
              { x: fromX, opacity: 0 },
              { x: 0, opacity: 1, duration: 0.6, ease: 'power2.out' },
              `-=0.5`
            );
          }
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

  const toggleItem = (index: number) => {
    setOpenIndex(openIndex === index ? null : index);
  };

  return (
    <section
      ref={sectionRef}
      id="faq"
      className="relative py-32 px-8 lg:px-16 bg-black overflow-hidden"
    >
      <div className="max-w-4xl mx-auto">
        {/* Section title */}
        <h2
          ref={titleRef}
          className="text-h1 lg:text-display-xl text-white font-medium text-center mb-20"
        >
          {faqConfig.title}
        </h2>

        {/* FAQ items */}
        <div className="space-y-0">
          {faqConfig.faqs.map((faq, index) => (
            <div
              key={index}
              ref={(el) => {
                itemsRef.current[index] = el;
              }}
              className={`relative ${
                index % 2 === 0 ? 'lg:-translate-x-8' : 'lg:translate-x-8'
              }`}
            >
              {/* Question */}
              <button
                className={`w-full py-6 lg:py-8 flex items-center justify-between text-left border-b transition-all duration-300 ${
                  openIndex === index
                    ? 'border-white'
                    : 'border-white/20 hover:border-white/40'
                } ${
                  openIndex !== null && openIndex !== index
                    ? 'opacity-50'
                    : 'opacity-100'
                }`}
                onClick={() => toggleItem(index)}
              >
                <h3
                  className={`text-h5 lg:text-h4 text-white pr-8 transition-all duration-200 ${
                    openIndex === index ? 'font-medium' : 'font-normal'
                  }`}
                >
                  {faq.question}
                </h3>

                {/* Plus icon */}
                <div
                  className={`flex-shrink-0 w-10 h-10 rounded-full border border-white/30 flex items-center justify-center transition-all duration-400 ${
                    openIndex === index
                      ? 'bg-highlight border-highlight rotate-45 scale-120'
                      : 'hover:border-white'
                  }`}
                >
                  <Plus className="w-5 h-5 text-white" />
                </div>
              </button>

              {/* Answer */}
              <div
                className={`overflow-hidden transition-all duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                  openIndex === index
                    ? 'max-h-[500px] opacity-100'
                    : 'max-h-0 opacity-0'
                }`}
              >
                <div className="py-6 lg:py-8">
                  <p className="text-body-lg text-white/70 leading-relaxed">
                    {faq.answer}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
