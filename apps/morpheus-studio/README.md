# Photographer Style Template

A high-end, dark-themed single-page website template with dramatic animations, built for photographers, creative studios, and visual artists. Features GSAP-powered scroll animations, 3D transforms, particle effects, custom cursor, and a noise texture overlay.

## Features

- Full-screen hero with chromatic aberration effect
- GSAP ScrollTrigger-powered scroll animations throughout
- 3D card transforms and parallax scrolling
- Interactive custom cursor (desktop)
- Canvas-based floating particle field
- Noise texture overlay for cinematic feel
- Typewriter, blur-reveal, clip-path, and elastic animations
- Floating image preview on service hover
- Animated price counter
- Marquee text in footer with highlighted characters
- Fully responsive with mobile menu
- Reduced motion support

## Tech Stack

- React 19 + TypeScript
- Vite
- Tailwind CSS 3
- GSAP (ScrollTrigger)
- Lucide React (icons)
- Geist font (Google Fonts)

## Project Structure

```
src/
  config.ts           # All content configuration (edit this file)
  App.tsx             # Root component
  App.css             # Default Vite styles (unused)
  index.css           # Global styles, CSS variables, animations
  main.tsx            # Entry point
  sections/
    Hero.tsx          # Full-screen hero with parallax
    About.tsx         # About section with dual images + author bio
    Works.tsx         # Portfolio grid with 3D card hover
    Services.tsx      # Service list with floating image preview
    FAQ.tsx           # Accordion FAQ with alternating layout
    Testimonials.tsx  # 3D testimonial cards
    Pricing.tsx       # Pricing plans with animated counter
    Blog.tsx          # Blog posts with clip-path reveal
    Contact.tsx       # Contact form with diagonal divider
    Footer.tsx        # Footer with marquee and link columns
  components/
    Navigation.tsx    # Fixed navbar with mobile menu
    CustomCursor.tsx  # Custom red cursor (desktop only)
    ParticleField.tsx # Canvas particle animation
    ui/               # shadcn/ui components
  hooks/
    useInView.ts      # Intersection Observer hook
    useMousePosition.ts # Mouse position tracker
    use-mobile.ts     # Mobile breakpoint detection
  lib/
    utils.ts          # Utility functions
public/
  images/             # Place your images here
```

## Quick Start

1. Install dependencies:
   ```bash
   npm install
   ```

2. Edit `src/config.ts` with your content

3. Add images to `public/` directory

4. Start development server:
   ```bash
   npm run dev
   ```

5. Build for production:
   ```bash
   npm run build
   ```

## Configuration

All content is configured in `src/config.ts`. Each section has its own config object with TypeScript interfaces.

### siteConfig

| Field | Type | Description |
|---|---|---|
| title | string | Browser tab title |
| description | string | Site meta description |
| language | string | HTML lang attribute (e.g. "en", "zh-CN") |

### navigationConfig

| Field | Type | Description |
|---|---|---|
| logo | string | Brand name shown in navbar |
| items | NavItem[] | Navigation links `{ label, href }` |

### heroConfig

| Field | Type | Description |
|---|---|---|
| title | string | Large hero title (split into animated characters) |
| subtitle | string | Subtitle below title |
| backgroundImage | string | Hero background image path |
| servicesLabel | string | Vertical text on left side |
| copyright | string | Copyright text bottom-right |

### aboutConfig

| Field | Type | Description |
|---|---|---|
| titleLine1 | string | First line of section title |
| titleLine2 | string | Second line of section title |
| description | string | About paragraph |
| image1 | string | Primary image path |
| image1Alt | string | Alt text for image 1 |
| image2 | string | Secondary image path (overlapping) |
| image2Alt | string | Alt text for image 2 |
| authorImage | string | Author portrait path |
| authorName | string | Author name |
| authorBio | string | Author biography (animated char by char) |

### worksConfig

| Field | Type | Description |
|---|---|---|
| title | string | Section title (animated per character) |
| subtitle | string | Section subtitle |
| projects | WorkItem[] | Array of `{ id, title, category, image }` |

### servicesConfig

| Field | Type | Description |
|---|---|---|
| title | string | Section title |
| subtitle | string | Section subtitle |
| services | ServiceItem[] | Array of `{ id, title, description, image }` |

### testimonialsConfig

| Field | Type | Description |
|---|---|---|
| title | string | Section title |
| testimonials | TestimonialItem[] | Array of `{ id, name, title, quote, image }` |

### pricingConfig

| Field | Type | Description |
|---|---|---|
| title | string | Section title |
| subtitle | string | Section subtitle |
| ctaButtonText | string | Button text on each card |
| plans | PricingPlan[] | Array of `{ id, name, price, unit, featured, features }` |

### faqConfig

| Field | Type | Description |
|---|---|---|
| title | string | Section title |
| faqs | FAQItem[] | Array of `{ question, answer }` |

### blogConfig

| Field | Type | Description |
|---|---|---|
| title | string | Section title |
| subtitle | string | Section subtitle |
| allPostsLabel | string | "View all" button text |
| readMoreLabel | string | "Read more" link text |
| readTimePrefix | string | Prefix before read time (e.g. "Read ") |
| posts | BlogPost[] | Array of `{ id, title, excerpt, readTime, date, image, category }` |

### contactConfig

| Field | Type | Description |
|---|---|---|
| title | string | Section title (animated per character) |
| subtitle | string | Section subtitle |
| nameLabel | string | Name field label |
| emailLabel | string | Email field label |
| projectTypeLabel | string | Project type field label |
| projectTypePlaceholder | string | Select placeholder |
| projectTypeOptions | ContactFormOption[] | Array of `{ value, label }` |
| messageLabel | string | Message field label |
| submitButtonText | string | Submit button text |
| image | string | Side image path |

### footerConfig

| Field | Type | Description |
|---|---|---|
| marqueeText | string | Scrolling marquee text |
| marqueeHighlightChars | string[] | Characters to highlight in red |
| navLinks1 | FooterLink[] | First column links `{ label, href }` |
| navLinks2 | FooterLink[] | Second column links `{ label, href, icon? }` |
| ctaText | string | CTA link text |
| ctaHref | string | CTA link href |
| copyright | string | Copyright line |
| tagline | string | Tagline next to copyright |

## Required Images

Place these images in the `public/` directory:

| Image | Usage | Recommended Size |
|---|---|---|
| hero-main.jpg | Hero background | 1920x1080+ |
| about-1.jpg | About section primary | 800x1200 |
| about-2.jpg | About section secondary | 600x900 |
| photographer.jpg | Author portrait | 400x400 |
| work-1.jpg to work-4.jpg | Portfolio items | 800x1067 (3:4) |
| service-1.jpg to service-4.jpg | Service preview images | 600x800 |
| testimonial-1.jpg to testimonial-3.jpg | Testimonial avatars | 400x400 |
| blog-1.jpg, blog-2.jpg | Blog post covers | 1200x675 (16:9) |
| contact.jpg | Contact section image | 800x1200 |

## Design

### Colors
- **Background**: Black (#000000)
- **Foreground**: White (#ffffff)
- **Highlight/Accent**: Red (#ea0000)
- **Dark Gray**: #1a1a1a
- **Deep Black**: #0a0a0a

### Typography
- **Font**: Geist (Google Fonts)
- **Display XXL**: 168px
- **Display XL**: 126px
- **H1**: 71.66px
- **H2**: 59.72px
- **H3**: 49.77px
- **H4**: 41.47px
- **H5**: 34.56px
- **H6**: 28.8px
- **Body Large**: 20px
- **Body**: 16px
- **Body Small**: 14px

### Animation Easings
- Dramatic: `cubic-bezier(0.87, 0, 0.13, 1)`
- Elastic: `cubic-bezier(0.68, -0.55, 0.265, 1.55)`
- Smooth: `cubic-bezier(0.4, 0, 0.2, 1)`
- Expo Out: `cubic-bezier(0.16, 1, 0.3, 1)`

## Notes

- All sections return `null` when their config is empty, so unused sections are automatically hidden
- The custom cursor is hidden on touch devices
- Particle count is reduced on touch devices for performance
- The `language` field in `siteConfig` controls the HTML `lang` attribute
- Footer link icons are mapped by name: currently supports "Instagram" and "Dribbble"
