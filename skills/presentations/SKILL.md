# Presentations

This skill generates presentation deliverables in many shapes. Treatments (commercial director's pitches) are one shape. Other shapes include lookbooks, film/TV pitch decks, strategic proposals, public bid microsites, sponsorship decks, capabilities decks, brand strategy decks, playbooks, status reports, editorial long-form scrollytelling, portal constellations, personal sites, brand landings, itineraries, and fully custom one-offs. Mood reels (video sizzle) live in the `video-editing` skill, not here. The bundled scroll-section pipeline (GSAP plus ScrollTrigger) is the most common shape, not the only one. PDF export via Playwright. Video export via ffmpeg x11grab (recording an HTML deck as motion, not cutting reference footage).

## Discovery Protocol (MANDATORY before building)

**This skill is large. It has many capabilities and many ways to assemble them. Ask first. Do not default.**

The bundled scroll-section grammar is one tool among several. Choosing the wrong shape wastes a day. Before generating anything, walk Nick through these questions one branch at a time. Provide your recommendation alongside each question. Stop and confirm before reaching for `create_presentation.py`.

1. **Subcategory.** Which shape from the **Subcategories** map below: treatment, lookbook, mood reel (cross-skill), film/TV pitch deck, strategic proposal, public bid microsite, sponsorship deck, capabilities deck, brand strategy deck, playbook, status report, editorial long-form, portal constellation, personal site, brand or artist landing, itinerary, or fully custom?
2. **Form.** Continuous scroll, pinned sections, single screen with state, interactive surface, or multi-page?
3. **Register.** Cinematic and mood-first, technical and dense, editorial and literary, business and clean, or playful and toy-like?
4. **Audience.** Producer or creative director? Client or sponsor? Internal team? Public viewer?
5. **Length.** Single screen, short scroll, long scroll, or multi-chapter with navigation?
6. **Output.** HTML only, plus PDF, plus video, or live surge deployment?
7. **Brand.** Existing brand voice (`--design-md` or `--brand-url`), in-house cinematic mood (`--aesthetic`), or fully custom?

The default scroll-section pipeline fits cleanly when the answer set is roughly "treatment, continuous scroll, cinematic, producer audience, long scroll, HTML plus video, in-house aesthetic". Most other configurations either need significant deviation from the JSON schema or a hand-built one-off. Be honest about this. If the right deliverable is fundamentally not a scrolling document, build it custom.

**Skip the protocol** when Nick has already specified the shape ("make me a treatment", "build a portal constellation like the CooCoo deck"). The protocol prevents building the wrong shape. It does not slow down clear briefs.

## Subcategories

Each shape below is grounded in two sources: the industry-standard form as it is actually practiced (sources cited inline, all consulted via web research not just memory) and the in-house artifacts shipped from this directory tree (file paths included for verification). The two are listed separately so you can tell *industry convention* from *Nick's particular take on it*.

### Treatment (Director's Treatment for Commercial)

**Industry form.** A multi-page prose document a director sends to an agency creative director and the client to win a TVC pitch. Tells the story in present-tense action, layers in director's vision (tone, style, approach), and pairs reference frames or stills with paragraphs of intent. Standard sections in commercial work include cover, director's note (the "why me / why now"), concept, beats or scenes, visual approach with reference images, cinematography, casting/talent, locations, wardrobe, music, post, packshot, and closing. The technical-execution sections are unusually heavy compared to feature treatments because the agency needs to know *how* the director will hit the shoot dates and budget. Treatments are typically delivered as a designed PDF or a Readymag scrolling page; in 2026 the scrolling page is increasingly the default for ambitious directors. Often paired with a budget document where the director justifies any over-spec spend.
*References: [Assemble — How to Write a Winning Director's Treatment](https://www.onassemble.com/blog/how-to-write-a-winning-directors-treatment), [The Collective Pitch — Treatment for Commercial step-by-step](https://thecollectivepitch.com/portfolio/treatment-for-commercial/), [The Gate Films — What to Look for in a Director's Treatment](https://www.thegatefilms.com/blog/film-craft-101-what-to-look-for-in-a-directors-treatment).*

**In-house references.** Greek industry PDFs at `~/projects/shared/presentations/treatments/*.pdf` (Topcut Modiano, Loumidis Maroudis, PARRANO Papaioannou, AKTOR, fresh_pedro_abreu). Topcut DEH Vasilis Kekatos as a Readymag-exported HTML treatment. Methodology at `~/projects/shared/presentations/TREATMENT_METHODOLOGY.md`.

- **Past work:** Arrena water TVC at `~/projects/shared/presentations/treatments/arrena/`. Built with this skill.
- **Tooling:** `create_presentation.py` with full scroll-section JSON, `--aesthetic arrena` or `synedark` for in-house mood, `blur` or `smooth` animation, `nav: none`, video export for sending the file as motion.
- **Distinguishing test:** "Am I a director communicating creative vision to a creative director, with a script already in hand?" If yes → treatment. If the script doesn't exist yet, it's probably a lookbook or pitch deck.

### Lookbook (Mood Document)

**Industry form.** Visual-only document — no prose, no beats. Compiled film stills, photography, palettes, fabrics, location references, grouped by tone or location or costume or lighting. Used (a) by the director as an internal alignment document with cinematographer / production designer / wardrobe before the shoot, and (b) as a pitching aid earlier in the funnel than a treatment, to get mood approval before script development. Distinct from a *pitch deck* (which is the financiers' document with logline + budget) and from a *mood reel* (which is the video version, sometimes called a ripomatic or sizzle-style cut).
*References: [Filmmaker Magazine — Mood Reels and Lookbooks: The Image Comes First](https://filmmakermagazine.com/66393-the-image-comes-first/), [Sarah Cogan — The Difference Between Your Film's Lookbook and Pitch Deck](https://www.sarahcogan.com/blog/the-difference-between-your-films-lookbook-and-a-pitch-deck), [Get It Made — Look Book, Pitch Deck & Pitch Bible](https://www.getitmade.la/resource-center/p/pitch-deck).*

**In-house references.** None shipped standalone. The visual-reference sections inside the Arrena treatment serve this function in-document.

- **Tooling:** `create_presentation.py` with heavy `image_grid`, `full_bleed`, and `image` sections, sparse `divider`, `nav: none`, animation `blur` or `smooth`. Sits inside the existing skill grammar.
- **Distinguishing test:** "Would adding a paragraph of prose dilute this?" If yes → lookbook. If you need to articulate the *why* in language → treatment.

### Mood Reel / Sizzle (cross-skill — lives in `video-editing`, not here)

**Industry form.** Edited video that uses existing footage (films, stock, past work, b-roll) cut to music to establish tone before any original footage exists. "Ripomatic" is the older term. Used in early-stage pitches when the lookbook is not enough and a treatment is premature. Increasingly common as Instagram/Vimeo-native deliverables.
*References: [Filmmaker Magazine — same piece above](https://filmmakermagazine.com/66393-the-image-comes-first/), [No Film School — Joe Carnahan's Daredevil mood/tone film](https://nofilmschool.com/2012/08/director-sizzle-reel-mood-tone-film-joe-carnahan-daredevil), [Ken Aguado — All About Sizzle Reels](https://medium.com/@ken.aguado/all-about-sizzle-reels-55b5b420ebb).*

- **Tooling:** Out of scope for this skill. The `video-editing` skill (ffmpeg + moviepy) handles cut-to-music edits with reference footage. This skill's `--video` export is for *recording a finished HTML deck as motion*, not for cutting reference footage.
- **Distinguishing test:** "Is the deliverable a video file, not a document?" Then send the request to `video-editing`, not here.

### Film/TV Pitch Deck

**Industry form.** Distinct from a treatment. The pitch deck is the *financiers' document* — used to pitch a feature or series to studios, financiers, or platforms; the treatment is the *production document* — used after a director attaches to a project. Standard pitch deck contents: logline (one sentence), synopsis, character cards, comparable titles ("comps"), pilot outline, season-one episode breakdown, future-season outlines (for series), packaging info (attached talent), budget top-sheet or range, financial projections. Almost always paired with a longer lookbook for visual style. Length varies widely — 10 pages for a tight feature pitch, 30+ for a full series bible.
*References: [Sarah Cogan — Lookbook vs Pitch Deck](https://www.sarahcogan.com/blog/the-difference-between-your-films-lookbook-and-a-pitch-deck), [Vicious & Co — Perfect TV and Film Pitch Deck Examples](https://viciousandco.com/film-and-tv-pitch-deck-examples/), [LA Film School — Ultimate Guide to Creating Your Pitch Deck](https://www.lafilm.edu/blog/the-ultimate-guide-to-creating-your-films-pitch-deck/), [Guerrilla Rep Media — 12 Slides You Need in Your IndieFilm Investment Deck](https://www.theguerrillarep.com/blog/the-12-slides-you-need-in-your-indiefilm-investment-deck-with-template).*

**In-house references.** None shipped. Adjacent: the Valve Application directory contains drafts but is a job application, not a project pitch.

- **Tooling:** `create_presentation.py` with `nav: topbar`, mixed `concept` + `cards` + `image_grid` + `stats` (for budget/projections) + `closing`. Lookbook-quality images do most of the work; treatment-style prose stays sparse.
- **Distinguishing test:** "Does this document end with 'how much money do we need'?" If yes → pitch deck. If it ends with 'here is how I will direct it' → treatment.

### Strategic Proposal (Confidential B2B Partnership)

**Industry form.** Privately delivered persuasion document for a discrete partnership decision (joint venture, real-estate development partnership, exclusive supplier agreement, multi-year sponsorship outside the standard tier ladder). Industry conventions: 4–7 pages for B2B with multiple services, 2–3 for simpler asks; structured as opportunity → counterparty value → terms → confidentiality. Always includes confidentiality / NDA framing because the document references unannounced strategy. Tone: more architecture than advertising, clean rather than cinematic, often with section numbers because legal will reference them.
*References: [StoryDoc — Writing a Partnership Proposal](https://www.storydoc.com/blog/how-to-write-a-partnership-proposal), [Qwilr — How to Write a Partnership Proposal to Stand Out](https://qwilr.com/blog/how-to-write-partnership-proposal/), [Ignitec — Strategic Partnership Proposal Template](https://www.ignitec.com/insights/a-strategic-partnership-proposal-template-to-foster-innovation-and-boost-business-growth/).*

**In-house references.** Alexandrio 2.0 (ΚΑΕ Άρης × ΔΕΘ-HELEXPO) at `~/projects/clients/aris-bc/alexandrio-2-0/site/`. Sections: Hero, Proposal, Concept, Space Request, Why ΔΕΘ Wins, Revenue, Traffic, Brand, Benefits, Closing.

- **Tooling:** `create_presentation.py` with `nav: topbar`, `mode: dark`, client brand color, hero with video or full-bleed, stats sections, benefit tables, formal closing. Surge with non-public URL.
- **Distinguishing test:** "Is this a one-shot persuasion artifact for a counterparty board, with confidentiality implications?" Then strategic proposal, not sponsorship deck (which sells from a tier menu) and not bid microsite (which is public-facing).

### Public Bid Microsite / Bid Book

**Industry form.** Public-facing pitch hosted on its own URL, responding to an open call (host city bid, open RFP, public tender, festival programming pitch). Modern Olympic-style bid books are submitted in three IOC stages — Vision/Concept/Strategy → Governance/Legal/Funding → Delivery/Experience/Legacy — where the first two are textual and detailed and the third is more inspirational. The same shape transfers to phygital sports bids, festival hosting, civic competitions, and large-format RFPs. Verified data carries the argument; numbers must be auditable. Hero video, animated counters tied to real sources, anchored sections, single-page CTA.
*References: [IOC Candidature Process Olympic Games 2024 (PDF)](https://stillmed.olympic.org/Documents/Host_city_elections/Candidature_Process_Olympic_Games_2024.pdf), [Olympics Watch — All the LA 2024/2028 Bid Books](https://olympicswatch.org/2021/01/21/all-the-la-2024-2028-bid-books-in-one-place/), [Maurizio La Cava — How to Create a Winning Olympic Bid Presentation](https://www.mauriziolacava.com/en/how-to-create-a-winning-presentation-for-the-olympic-bid/).*

**In-house references.** Games of the Future host city bid at `~/projects/clients/gotf-bid-site/`, live at `host-the-future.surge.sh`. Sections: Hero, Momentum (Ipsos data), Experience, Value, Closing. Animated counters tied to audited audience numbers.

- **Tooling:** `create_presentation.py` with `mode: dark`, `nav: dots` or none, hero looping video, `stats` sections with `data-count` for animated counters, surge deployment.
- **Distinguishing test:** "Is the URL itself part of the deliverable, sent to multiple stakeholders or a committee?" Then bid microsite. If the document is private and individually addressed → strategic proposal.

### Sponsorship Deck

**Industry form.** Sales deck used by sports rights holders, festivals, properties, and creators to sell sponsorship to brand partners. Five-section structure is the industry default: (1) overview / opportunity, (2) brand description with history + values + future goals, (3) value to sponsor with target audience demographics + activation plans, (4) tier/package menu with pricing, (5) appendix. Length: ~35 slides max with a heavier appendix for asset-by-asset breakdowns. Image-to-text ratio runs ~60/40 graphics. Audience customization matters: C-suite sees brand alignment + KPIs, marketing experts see asset variety + cost, creative marketers see how the property supports campaigns. Distinct from strategic proposal because the offer is structured rights from a tier menu, not a one-off partnership.
*References: [PandoPartner — Building the Best Sponsorship Sales Deck](https://pandopartner.com/blog/building-the-best-sponsorship-sales-deck), [Power Sponsorship — Best Layout of a Sponsorship Deck](https://powersponsorship.com/best-layout-sponsorship-deck/), [The DigiDeck — How to Make a Sponsorship Pitch Deck](https://www.thedigideck.com/sponsorship-pitch-deck/), [Visme — 15 Strategic Sports Sponsorship Deck Templates](https://visme.co/blog/sponsorship-deck-templates/).*

**In-house references.** None shipped standalone. ARIS BC has a content playbook (different shape) but not a sponsorship deck.

- **Tooling:** `create_presentation.py` with `nav: topbar`, `mode: light` or `dark` matching the property, repeated `cards` and `table` sections for tier breakdowns, `stats` for audience numbers, `image_grid` for activation examples.
- **Distinguishing test:** "Is the ask 'commit to a tier from this menu'?" Then sponsorship deck. If the ask is bespoke → strategic proposal.

### Capabilities Deck (Agency / Production House)

**Industry form.** Identity-and-services document a production company, agency, studio, or freelance shop sends to a prospective client to introduce capabilities and win RFPs or first meetings. Standard structure: (1) intro and value proposition framed around the client's problem, (2) services overview written as mini-stories not bullet lists, (3) process / approach in digestible steps, (4) case studies and social proof with at least one micro-case-study per claim, (5) measurable results with logos, (6) support and next steps. Length is typically 15–25 slides; "one-pager" is a separate cover surface used to open a longer deck or as a leave-behind. Tone is minimalist on copy, persuasive on outcomes. *"Mini-stories that show transformation, not just task delivery"* is the most-cited single principle.
*References: [InkNarrates — How to Make a Capabilities Deck](https://www.inknarrates.com/post/capabilities-deck), [HubSpot — 7 Secrets of a Winning Capabilities Presentation](https://blog.hubspot.com/sales/capabilities-presentation), [Stryve — Why Your Firm Needs a Capabilities Deck](https://www.stryvemarketing.com/blog/why-professional-services-firm-needs-capabilities-deck/), [Catapult — How to Craft a Winning Agency Capabilities Deck](https://catapultnewbusiness.com/how-to-craft-a-winning-agency-capabilities-deck/).*

**In-house references.** None shipped standalone for CooCoo AI as a full capabilities deck. The CooCoo Workflows portal-constellation deck is *one half* of a capabilities deck — the work-and-process half — without the value-prop, services, or commercial sections.

- **Tooling:** `create_presentation.py` with `nav: topbar`, `mode: dark`, opening `concept` + `note` for value prop, `cards` for services, `beats`-style or `stats` for case studies, `closing` with contact + reel CTA. The Quick Start treatment scaffold can be re-cut for this shape.
- **Distinguishing test:** "Is this introducing a company to potential clients in general, not pitching a specific project?" Then capabilities deck. The CooCoo Workflows deck is *not* this — it's a sub-section of one.

### Brand Strategy Deck

**Industry form.** Agency / consultancy deliverable that lays out positioning, messaging, and visual identity for a client brand. Standard frameworks include brand archetypes, positioning matrices, brand pyramids, and StoryBrand. Sections typically cover: (1) audience / problem space, (2) competitive landscape map, (3) positioning recommendation, (4) messaging architecture (purpose, mission, values, voice principles), (5) visual identity exploration, (6) activation rollout. Tone is consultative, evidence-based, framework-driven — not cinematic. Often delivered as a designed PDF or Figma deck rather than scrolling HTML, but a scrolling presentation can carry it well when the agency wants to *demonstrate* taste, not just describe it.
*References: [Insight to Action — 4 Types of Brand Strategy Consulting](https://itoaction.com/4-types-of-brand-strategy-consulting-which-is-best-for-you/), [Vivaldi — Brand Strategy Consulting Services](https://vivaldigroup.com/service/brand-strategy/), [NMS Consulting — Brand Strategy Guide](https://nmsconsulting.com/brand-strategy-consulting-guide-2025/).*

**In-house references.** None shipped standalone. Adjacent: ARIS BC playbook contains the *output* of brand strategy (Tone of Voice, Messaging Pillars) but not the *recommendation arc*.

- **Tooling:** `create_presentation.py` with `nav: sidebar` or `topbar`, mixed `concept` + `cards` + `table` (positioning matrix) + `two_col` (before/after positioning) + `image_grid` (visual identity exploration). The `--design-md` library (Linear, Stripe, etc.) becomes high-leverage here as a *reference* of well-executed brand systems to point at.
- **Distinguishing test:** "Is the deliverable a *recommendation about how the brand should show up*, with a framework backing the recommendation?" Then brand strategy deck. If it's a reference for already-decided rules → playbook.

### Playbook (Operational Reference)

**Industry form.** Long-form internal-and-partner reference document used over time, not for a single decision. Sections cover stable rules (brand DNA, tone of voice, content tiers, platform roles, do/don't). Sidebar navigation with section tracking, density-over-narrative, restrained animation. Closer to a Figma documentation site or a Notion-style internal wiki than to a deck. Read repeatedly across teams, often the source of truth that a content marketer or social manager opens weekly.

**In-house references.** ARIS BC Social Media Playbook at `~/projects/clients/aris-bc/aris-playbook/site/`. 16 sections covering Brand DNA, Core Narrative, Messaging Pillars, Tone of Voice, Visual Philosophy, Content Types, Platform Roles, Follow Policy, Interaction Rules, DM Policy, External Interaction, Moderation, Hashtags, Practical Do/Don't, Quick Reference.

- **Tooling:** `create_presentation.py` with `nav: sidebar`, narrow content max-width (~920px), `mode: dark` or `editorial`, table-heavy sections, restrained animation.
- **Distinguishing test:** "Will this be referenced repeatedly over the next year?" Then playbook. If it's a one-shot persuasion artifact → strategic proposal or capabilities deck.

### Status / Capabilities Report

**Industry form.** Hybrid of a financial report and a capabilities update. Sent to owners, partners, board, or close collaborators as a snapshot of the entity's state — income, projects shipped, team additions, runway. Tone is operational, dense, factual; cinematic register would feel evasive. Often bilingual when the audience spans markets.

**In-house references.** CooCoo AI status at `~/projects/coocoo-ai/coocoo-status/` (English plus Greek versions). Income line by line, hairline borders, large light-weight type, six-column max width.

- **Tooling:** Custom HTML closer to a finance report than a deck. The default skill grammar fights this register. A `minimal` plus `editorial` blend or hand-built HTML works better.
- **Distinguishing test:** "Is the audience an internal stakeholder who needs a numbers-first snapshot of state?" Then status report, not capabilities deck (which sells outward) and not playbook (which prescribes rules).

### Editorial Long-Form (Literary Scrollytelling)

**Industry form.** Magazine-style scrolling article. The format was canonized by [Snow Fall: The Avalanche at Tunnel Creek](https://www.nytimes.com/projects/2012/snow-fall/) (NYT, 2012; Pulitzer 2013) and developed further by The Guardian's [Firestorm](https://www.theguardian.com/world/interactive/2013/may/26/firestorm-bushfire-dunalley-holmes-family), [The Pudding](https://pudding.cool/) (data-narrative scrollytelling), NBC's editorial features (racial-segregation maps), and [Christie's](https://www.christies.com/) auction house deep-dives. Vocabulary: scroll-triggered media (text + image swap), animated charts, parallax photography, color shifts on scroll, sticky chapter titles, generous serif typography, chapter dividers as full-bleed images. The form serves *literary or investigative* content where pacing and immersion matter more than information density. Tools: Shorthand, Maglr, custom GSAP+ScrollTrigger.
*References: [Shorthand — Is Scrollytelling the Future of Digital Content?](https://shorthand.com/the-craft/an-introduction-to-scrollytelling/index.html), [Shorthand — 12 Engaging Scrollytelling Examples](https://shorthand.com/the-craft/engaging-scrollytelling-examples-to-inspire-your-content/), [Maglr — 10 Best Scrollytelling Examples](https://www.maglr.com/blog/best-scrollytelling-examples), [The Pudding](https://pudding.cool/).*

**In-house references.** None shipped, but `mode: editorial` is built for this register and the `cahiers` / `criterion` / `dazed` / `opus` aesthetics map to literary-magazine surfaces.

- **Tooling:** `create_presentation.py` with `mode: editorial`, `--aesthetic cahiers` / `criterion` / `dazed` / `opus`, `note` and `concept` sections heavy on text, `full_bleed` chapter dividers, sparse `image_grid`, animation `smooth` or `blur`.
- **Distinguishing test:** "Is this a literary, investigative, or critical piece where the reader settles in for 5+ minutes of focused reading?" Then editorial scrollytelling. If it's pitch-shaped → wrong category.

### Portal / Constellation (Interactive Hub)

**Industry form.** This shape doesn't have a single canonical name in the industry. It's adjacent to interactive case-study microsites (think award-show feature pages on [Awwwards](https://www.awwwards.com/) or [The FWA](https://thefwa.com/)), interactive hub navigations on agency portfolio sites, and interactive annual reports. The defining property: items presented as gestural objects (circles, tiles, video cutouts, character portraits) that morph or expand into modal content on click. The macro gesture *is* the message. Deep-linking and shareability of individual portals usually matter.

**In-house references.** CooCoo Workflows at `~/projects/coocoo-ai/coocoopresentation/`, live at `coocoo-workflows.surge.sh`. Four circular portals in one row, each with a silent video loop, click expands into a fullscreen modal with video, caption, and specs.

- **Tooling:** Custom HTML build. The skill's section vocabulary cannot carry this. Lenis for smooth scroll, GSAP for portal morph and modal expansion, hand-written pointer-event hierarchies for the layered modal stage.
- **Distinguishing test:** "Is the navigation the experience itself?" Then portal/constellation, custom build only.

### Personal Site / Portfolio

**Industry form.** Long-running portfolio site for an individual creator. Sections like Hero, Reel/Selected Work, About, Contact. Persistent rather than pitch-specific. Visual identity drives the design more than copy. Standard for film directors (Karolos Berahas pattern), composers, photographers, designers, architects.

**In-house references.** Nick Athens composer site at `~/projects/archive/nick-athens-site/`. Karolos Berahas director site at `~/projects/archive/karolos-berahas-site/`.

- **Tooling:** Hand-built HTML and CSS, sometimes Hugo. The skill grammar is too pitch-shaped — a portfolio is identity-shaped, not narrative-shaped.
- **Distinguishing test:** "Does this live indefinitely on a custom domain and represent the person, not a single project?" Then portfolio.

### Brand or Artist Landing

**Industry form.** Single-page landing site for a band, brand, product, or service. Hero, About, Music or Product, Shows or Use Cases, Contact. Brand presence at the entity level. Sometimes commerce-attached.

**In-house references.** Durydava band site at `~/projects/archive/durydava-site/`. CooCoo AI website at `~/projects/coocoo-ai/coocoo-ai-website/`, live at `coocooai.com`.

- **Tooling:** Hand-built or Hugo. The skill grammar can scaffold the dark cinematic shell but the core identity work is custom.
- **Distinguishing test:** "Public brand presence at the entity level, not a pitch *about* the entity?" Then brand landing.

### Itinerary / Plan / Reference

**Industry form.** Dense, table-driven utility document. Schedule, addresses, distances, links. No animation, no cinematic register. Notebook feel. Read on phone in motion.

**In-house references.** London Plan at `~/projects/personal/london-plan/`, live at `london-plan-nick.surge.sh`. Day-by-day schedule, area cards, restaurant lists, friend recommendations color-coded, embedded tube map.

- **Tooling:** Hand-built single-file HTML, dark with one accent color, tables and small type. The skill grammar overcomplicates this.
- **Distinguishing test:** "Read on phone in motion, structure beats narrative, small text fine?" Then itinerary.

### Custom (none of the above fits)

When a brief calls for a gesture the skill cannot carry — horizontal scrolling, before-after sliders, dashboard surfaces, OO-portal mechanics, page-turn interfaces, mapped story-driven scrollytelling, anything with its own interaction model — abandon the JSON pipeline and write the file by hand. Industry references for custom shapes are case-by-case lookups: [Awwwards](https://www.awwwards.com/), [Site Inspire](https://www.siteinspire.com/), [The FWA](https://thefwa.com/), [Httpster](https://httpster.net/).

- **Past work:** The CooCoo Workflows deck started here before adopting some skill conventions for type and color.
- **Distinguishing test:** "Did the conversation about shape keep returning to 'but what if we...'?" Then custom.

---

The shipped column is orientation, not constraint. Industry conventions are anchors, not rules. The right shape for new content is whatever serves new content — but knowing the conventions means *deviation is a choice*, not an accident. Confusing a treatment with a pitch deck, or a sponsorship deck with a strategic proposal, or a playbook with a brand strategy deck, is what produces the wrong deliverable.

## Quick Start

```bash
# Generate treatment from JSON definition
python skills/presentations/scripts/create_presentation.py \
    --json /tmp/treatment.json \
    --output /tmp/treatment.html

# With PDF export
python skills/presentations/scripts/create_presentation.py \
    --json /tmp/treatment.json \
    --output /tmp/treatment.html \
    --pdf /tmp/treatment.pdf

# Custom theme CSS
python skills/presentations/scripts/create_presentation.py \
    --json /tmp/treatment.json \
    --output /tmp/treatment.html \
    --theme /path/to/custom.css

# Seed scheme + fonts from a curated design system (Linear, Stripe, Apple, ...)
python skills/presentations/scripts/create_presentation.py \
    --json /tmp/treatment.json \
    --output /tmp/treatment.html \
    --design-md linear

# Record as video (auto-scroll + ffmpeg x11grab)
python skills/presentations/scripts/record_presentation.py \
    --html /tmp/treatment.html \
    --output /tmp/treatment.mp4

# Video with background music
python skills/presentations/scripts/record_presentation.py \
    --html /tmp/treatment.html \
    --output /tmp/treatment.mp4 \
    --audio /path/to/bg_music.mp3

# Custom resolution and cover hold time
python skills/presentations/scripts/record_presentation.py \
    --html /tmp/treatment.html \
    --output /tmp/treatment.mp4 \
    --width 1920 --height 1080 --fps 30 --delay 6
```

## JSON Structure

The document is defined by a top-level object with metadata, a cover, and an array of sections:

```json
{
    "title": "Document Title",
    "lang": "en",
    "particles": true,
    "cover": {
        "brand": "Brand Name",
        "brand_url": "https://brand.com",
        "logo": "/path/to/logo.png",
        "logo_filter": "brightness(2) invert(1)",
        "title": "Campaign Title",
        "type": "Director's Treatment",
        "meta": ["Director Name", "Production Co", "March 2026"],
        "background": "/path/to/cover-bg.jpg",
        "duration": 4.0
    },
    "scheme": {
        "bg": "#050505",
        "text": "#e8e4df",
        "text_dim": "rgba(255,255,255,0.45)",
        "text_mid": "rgba(255,255,255,0.7)",
        "accent": "#c9a84c",
        "accent_dim": "rgba(201,168,76,0.3)",
        "accent_light": "#e8d9a8",
        "brand_color": "#1b2d82",
        "brand_light": "#c5cff0",
        "cream": "#f5f0e8"
    },
    "fonts": {
        "heading": "Space Grotesk",
        "body": "Outfit",
        "serif": "Playfair Display"
    },
    "sections": [...]
}
```

### Top-Level Fields

- `title` — HTML page title
- `lang` — Language code (default: `en`)
- `particles` — Enable floating particle background (default: `true`)
- `mode` — Display mode: `dark` (default), `light`, `editorial`, `minimal`
- `animation` — Animation preset: `fade` (default), `slide`, `scale`, `blur`, `clip`
- `nav` — Navigation type: `none` (default), `sidebar`, `topbar`, `dots`, `progress`
- `cover` — Cover section (see below). Top-level, not inside `sections`.
- `scheme` — Color scheme overrides. Auto-derives `accent_dim`, `accent_light`, `brand_glow` from base colors if not set.
- `fonts` — Font overrides. Accepts any Google Font name (41 curated fonts with optimized specs, unknown fonts get default weights)

### Cover Object

The cover is full-viewport with animated entrance and scroll-fade-out.

- `brand` — Brand name text (top of cover)
- `brand_url` — Link for brand name
- `logo` — Logo image path (local = base64 embedded)
- `logo_filter` — CSS filter for logo (e.g. `"brightness(2) invert(1)"`)
- `title` — Subtitle below logo (serif italic)
- `type` — Document type label (e.g. "Director's Treatment")
- `meta` — Array of strings shown at bottom of cover
- `background` — Background image path
- `duration` — Seconds to hold on cover during video autoplay (default: `4.0`)

## Section-Level Timing (for Video Export)

Every section supports an optional `duration` field (seconds) that controls how long the section stays visible during video autoplay. If omitted, durations are auto-calculated:

| Section Type | Default Duration | Logic |
|---|---|---|
| `divider` | 2.5s | Fixed |
| `quote` | 4.0s | Fixed |
| `reveal` | 4.0s | Fixed |
| `full_bleed` | 3.5s | Fixed |
| `image` | 3.0s | Fixed |
| `image_grid` | 4.0s | Fixed |
| `stats` | 3.5s | Fixed |
| `packshot` | 5.0s | Fixed |
| `video` | 6.0s | Fixed |
| `closing` | 5.0s | Fixed |
| `hr` | 1.0s | Fixed |
| `note` | 3–15s | Word count at 180 wpm |
| `beats` | 3–15s | Word count at 180 wpm |
| `content` | 3–15s | Word count at 180 wpm |
| `concept` | 3–15s | Word count at 180 wpm |
| `two_col` | 3–15s | Word count at 180 wpm |
| `table` | 3–15s | 2 + 0.8s/row, capped |
| `cards` | 3–15s | Item count + text, capped |
| `why_list` | 3–15s | Word count |
| `specs` | 2 + 0.6s/item | Item count |

Override any default by setting `"duration": 8.0` on the section.

## Section Types

### Divider (section header)
```json
{"type": "divider", "number": "01", "title": "The Landscape"}
```
Numbered section header with accent line. Animates in on scroll.

### Note (director's note / long-form serif text)
```json
{
    "type": "note",
    "paragraphs": ["First paragraph...", "Second paragraph..."],
    "signature": "— Nick Athens"
}
```
Serif italic paragraphs, each animating in. Optional signature in accent color.

### Quote (dramatic centered text)
```json
{"type": "quote", "text": "The big quote in serif.", "sub": "Optional smaller text below"}
```
Large serif italic centered text with radial glow background.

### Table (competition / data table)
```json
{
    "type": "table",
    "caption": "Optional caption above table",
    "headers": ["Brand", "Share", "Positioning"],
    "rows": [
        ["Zagori", "22%", "Mountain purity"],
        ["**Arrena**", "**2%**", "**???**"]
    ]
}
```
Last row auto-highlights. Rows animate in with stagger.

### Cards (2-col or 4-col grid)
```json
{
    "type": "cards",
    "caption": "Optional header",
    "columns": 2,
    "items": [
        {"name": "Card Title", "desc": "Card description text"},
        {"name": "Card Title 2", "desc": "More text"}
    ]
}
```
- `columns`: 2 (gap-card style) or 4 (ref-card style, uses wide container)
- Also accepts `"description"` as alias for `"desc"`

### Reveal (dramatic text reveal)
```json
{
    "type": "reveal",
    "label": "THE GAP",
    "text": "Nobody owns authenticity.",
    "highlight": "authenticity",
    "sub": "This is the opportunity."
}
```
Centered dramatic text that scales in. `highlight` word gets gradient accent treatment.

### Full Bleed (full-width image with parallax)
```json
{"type": "full_bleed", "src": "/path/to/image.jpg", "alt": "Description"}
```
Full-width image with parallax scroll, edge-fade gradients, and scale-in animation.

### Concept (centered hero text)
```json
{
    "type": "concept",
    "heading": "The *Central* Idea",
    "desc": ["Paragraph one.", "Paragraph two."],
    "tagline": "The tagline"
}
```
Large serif heading with description paragraphs. Supports markdown in heading (`*italic*` renders in accent).

### Why List (numbered reasons)
```json
{
    "type": "why_list",
    "caption": "WHY THIS WORKS",
    "items": [
        {"title": "Reason One", "text": "Explanation"},
        {"title": "Reason Two", "text": "Explanation"}
    ]
}
```
Auto-numbered list with large ghost numbers.

### Beats (storyline / treatment scenes)
```json
{
    "type": "beats",
    "beats": [
        {
            "label": "Scene 1",
            "title": "The Opening",
            "text": "We see a mountain landscape...",
            "text2": "Optional second paragraph",
            "dialogue": "What the character says.",
            "action": "Stage direction."
        }
    ]
}
```
Multiple beats in one section. Dialogue renders in serif italic with accent left border. Also works as `"type": "beat"` with a single beat (no `beats` array wrapper needed). Optional `dialogue_style` on each beat sets custom inline CSS on the dialogue element.

### Two Column
```json
{
    "type": "two_col",
    "columns": [
        {"label": "Color Palette", "text": ["Warm naturals", "Golden hour light"]},
        {"label": "Atmosphere", "text": "Bright, authentic, real"}
    ]
}
```
`text` can be a string or array of strings. Optional `style` on the section sets custom inline CSS on the section element.

### Specs (key-value list)
```json
{
    "type": "specs",
    "items": [
        {"label": "Camera", "value": "ARRI Alexa Mini LF"},
        {"label": "Lenses", "value": "35-50mm Cooke Speed Panchros"}
    ]
}
```

### Stats (large numbers)
```json
{
    "type": "stats",
    "items": [
        {"number": "1.2B€", "label": "Market Size"},
        {"number": "22%", "label": "Leader Share"}
    ],
    "text": "Optional explanatory text below"
}
```

### Packshot (product hero shot)
```json
{
    "type": "packshot",
    "image": "/path/to/product.png",
    "taglines": ["Pure.", "Natural."],
    "logo": "/path/to/logo.png"
}
```
Full-viewport section with product image, serif taglines, and brand logo.

### Video (YouTube/Vimeo embed)
```json
{
    "type": "video",
    "label": "Reference Video",
    "url": "https://www.youtube.com/watch?v=xyz",
    "caption": "Optional caption"
}
```
YouTube and Vimeo URLs auto-convert to embed format.

### Closing (director info / credits)
```json
{
    "type": "closing",
    "role": "Director",
    "name": "Nick Athens",
    "links": "nickathens.com | IMDB",
    "brand_logo": "/path/to/logo.png",
    "company": "CooCoo AI",
    "company_url": "https://coocoo.com"
}
```

### Content (generic text block)
```json
{
    "type": "content",
    "caption": "OPTIONAL LABEL",
    "heading": "Optional Heading",
    "texts": ["Paragraph one.", "Paragraph two."],
    "align": "center"
}
```
Also accepts `"text"` (string) instead of `"texts"` (array).

### Image (single centered image)
```json
{"type": "image", "src": "/path/to/image.png", "caption": "Optional caption"}
```

### Image Grid (mood board)
```json
{
    "type": "image_grid",
    "caption": "VISUAL REFERENCES",
    "columns": 4,
    "images": [
        {"src": "/path/to/img1.jpg", "caption": "Reference 1"},
        {"src": "/path/to/img2.jpg", "caption": "Reference 2"}
    ]
}
```
`columns`: 2, 3, or 4.

### Horizontal Rule
```json
{"type": "hr"}
```

## Inline Markdown

Text fields support: `**bold**`, `*italic*`, `` `code` ``, `[link](url)`, `\n` line breaks.

## Modes

Four display modes define the surface system (background, text colors, component styling). The mode is the first creative decision for any presentation.

| Mode | Background | Text | Default Accent | When to Use |
|---|---|---|---|---|
| `dark` | #050505 (near-black) | #e8e4df (warm white) | #c9a84c (gold) | Treatments, cinematic pitches, premium brands, moody content |
| `light` | #faf8f5 (warm cream) | #1a1a1a (near-black) | #8b6914 (dark gold) | Business proposals, strategy docs, hospitality, architecture |
| `editorial` | #f8f5f0 (paper cream) | #1c1917 (charcoal) | #c0392b (deep red) | Publications, literary content, cultural institutions, reviews |
| `minimal` | #ffffff (pure white) | #000000 (pure black) | #000000 (black) | Technical proposals, portfolios, modernist brands |

The mode defines the surface. The `scheme` override customizes specific colors (accent, brand) on top of the mode. Same client can have different modes for different document types.

## Animation Presets

One preset per presentation. Defines how elements enter the viewport on scroll.

| Preset | Feel | Entrance | Best For |
|---|---|---|---|
| `fade` | Gentle, default | opacity + translateY(20px) | Universal, safe default |
| `slide` | Directional, dynamic | Alternating left/right translateX | Pitch decks, energetic content |
| `scale` | Dramatic, bouncy | scale(0.85) with back.out easing | Hero-heavy, product launches |
| `blur` | Cinematic, focus-pull | filter:blur(12px) clearing | Film treatments, luxury brands |
| `clip` | Sharp, editorial | clipPath inset reveal | Technical, editorial, architectural |
| `smooth` | Cinematic, calm | translateY(14px) + blur(4–6px) clearing on sine.out, 1.4s body / 1.8s heroes | Long-form decks, producer-facing tech presentations, anything where "snappy" reads as "twitchy" |

## Navigation

Five navigation types. Choose based on content structure and audience.

| Type | Appearance | Best For |
|---|---|---|
| `none` | No navigation, pure scroll | Treatments, short docs, video export |
| `sidebar` | Fixed left panel with section links | Long documents, playbooks, multi-chapter content |
| `topbar` | Fixed top bar with section links + progress | Business presentations, proposals |
| `dots` | Vertical dots on right edge | Minimal interference, portfolios |
| `progress` | Thin accent bar at top | Any length, subtle orientation |

Navigation items are auto-extracted from `divider` sections (number + title).

## Creative Direction

Before generating any presentation, make three conscious choices:

**1. Mode** (surface system)
Match the mode to the content's emotional register, not to a default. A sponsorship deck for a sports club can be dark. A strategy proposal for a cultural institution should probably be editorial. A tech company's capabilities deck might be minimal.

**2. Typography** (personality)
Never use the same font pairing twice in a row for different clients. The pipeline accepts any Google Font. Some proven pairings by context:

- **Cinematic/premium:** DM Serif Display + Outfit + Playfair Display
- **Corporate/clean:** Inter + Inter + Lora
- **Editorial/literary:** Cormorant Garamond + Source Serif 4 + EB Garamond
- **Tech/modern:** Space Grotesk + DM Sans + Spectral
- **Bold/energetic:** Unbounded + Plus Jakarta Sans + Libre Baskerville
- **Luxury/fashion:** Montserrat + Urbanist + Noto Serif Display
- **Brutalist/stark:** Archivo + Archivo + Space Mono
- **Friendly/startup:** Figtree + Nunito Sans + Crimson Pro

**3. Animation** (movement vocabulary)
Match the animation to the content's rhythm. A fast-paced sports proposal should use `slide`. A luxury brand treatment should use `blur`. A technical proposal should use `clip` or `fade`.

**What NOT to do:**
- Use dark mode + Space Grotesk + Outfit + Playfair Display + fade for everything
- Use the same navigation type for every document
- Default to gold accent when the brand has its own color

## Theme

Default theme: dark background, triple typography system (Space Grotesk / Outfit / Playfair Display), accent color system with brand color integration.

Theme CSS at: `skills/presentations/scripts/theme.css`

All styling uses CSS custom properties. Override via `scheme` in JSON, `mode` for surface presets, or provide a custom `--theme` CSS file.

## Video Export

Record any presentation as a cinematic video. Uses ffmpeg x11grab with CPU-based H.264 encoding (libx264) to capture headed Chromium on display :0.

**How it works:**
1. The HTML includes dormant auto-scroll JS (activated by `?autoplay=1` URL parameter)
2. `record_presentation.py` opens the HTML in Chromium, triggers autoplay, captures with ffmpeg x11grab
3. GSAP ScrollToPlugin smoothly scrolls section-by-section with `power2.inOut` easing
4. ScrollTrigger animations fire naturally as sections scroll into view
5. Encoded as H.264 MP4 via libx264

**Pacing control:** Set `duration` on individual sections to control video timing. Dramatic reveals should hold longer, data tables can be faster. The auto-scroll respects these per-section durations.

**Background audio:** Use `--audio` to mux in music. The video is trimmed to the shorter of video/audio (`-shortest`).

**Recording parameters:**
- `--width` / `--height` — Resolution (default: 1920x1080)
- `--fps` — Frame rate (default: 30)
- `--delay` — Seconds to hold on cover before scrolling (default: 5.0)

## Spec-to-JSON Workflow

Instead of constructing the JSON manually, describe the presentation as a natural language spec. The spec captures intent, tone, and structure — the JSON is generated from it.

### How to write a spec

A spec is a plain-English creative brief. Include:

1. **Purpose** — What is this document? (treatment, pitch, portfolio, brief)
2. **Audience** — Who will see it? (client, jury, investors, internal team)
3. **Tone** — How should it feel? (cinematic, corporate, playful, stark)
4. **Visual direction** — Colors, mood, reference images, typography preferences
5. **Content outline** — The narrative arc:
   - What's the opening statement / hook?
   - What data or context needs to be shown?
   - What's the core insight or concept?
   - How does the story unfold (beats, scenes)?
   - What's the closing / call to action?
6. **Pacing notes** — Which moments should hold (dramatic pause) vs. flow quickly
7. **Assets** — Logo paths, images, brand colors (hex values)

### Spec example

```
Treatment for Arrena water brand TVC pitch.

Audience: Creative director at the agency.
Tone: Cinematic, confident, quietly rebellious. Dark aesthetic.
Colors: Deep navy brand (#1b2d82), gold accent (#c9a84c), near-black background.

Structure:
- Open with brand logo on dark. Hold.
- Market landscape: table showing all Greek water brands, market share, positioning.
  Arrena is last row — highlighted, question mark on positioning.
- The gap: nobody owns "authenticity" in this space. Dramatic reveal.
- Director's note: 2 paragraphs on why authenticity matters now, personal voice.
- Concept: "Real Water, Real People" — hero text with tagline.
- 4 beats: mountain source, village life, the pour, the tagline moment.
- Visual references: 4-image mood board (earthy, warm, golden hour).
- Technical specs: camera, lenses, aspect ratio, color grade approach.
- Closing: director name, links, brand logo.

Pacing: Hold longer on the gap reveal and concept. Table and specs can be faster.
```

This spec contains everything needed to generate the full JSON definition. The mapping is direct:
- Market landscape → `table` + `stats`
- The gap → `reveal`
- Director's note → `note`
- Concept → `concept`
- Beats → `beats`
- Visual references → `image_grid`
- Technical specs → `specs`
- Closing → `closing`

## Brand Ingestion (Firecrawl)

Seed a treatment's `scheme`, `fonts`, and `cover.logo` from a live brand URL by passing `--brand-url`. The skill calls Firecrawl's v2 `/scrape` endpoint with the `branding` format, which is the dedicated design-system extractor (logo, colors, typography).

```bash
# Standalone — emit a JSON partial
python scripts/ingest_brand.py --url https://example.com --out brand.json

# Integrated — seed a treatment inline
python scripts/create_presentation.py \
    --json treatment.json \
    --brand-url https://example.com \
    --output out.html
```

**Setup.** Set `FIRECRAWL_API_KEY` in your shell env or in your project `.env`:

```
FIRECRAWL_API_KEY=fc-...
```

Free tier is 500 lifetime credits. One brand scrape = 1 credit.

**What gets extracted:**

| Firecrawl field | Maps to |
|---|---|
| `colors.background` | `scheme.bg` |
| `colors.textPrimary` | `scheme.text` |
| `colors.textSecondary` | `scheme.text_dim` |
| `colors.accent` / `colors.primary` | `scheme.accent` |
| `colors.primary` | `scheme.brand_color` |
| `typography.fontFamilies.heading` | `fonts.heading` |
| `typography.fontFamilies.primary` | `fonts.body` |
| `logo` / `images.logo` | `cover.logo` (downloaded to `~/.cache/presentations/brand_logos/`) |

**Precedence** (highest wins): explicit treatment JSON → `--design-md` → `--aesthetic` → `--brand-url`. Nothing in the treatment gets overwritten.

## Aesthetic References

A local library of cinematic references lives in `references/*.md`. Each file encodes a brand's visual vocabulary as YAML frontmatter (palette, fonts, mood, avoid) plus prose notes. Pass `--aesthetic <name>` to seed a treatment with that baseline.

```bash
# List available references
python scripts/references.py list

# Inspect one
python scripts/references.py show a24

# Apply while generating
python scripts/create_presentation.py \
    --json treatment.json \
    --aesthetic a24 \
    --output out.html
```

**Current references:**

| Name | Category | Mood |
|---|---|---|
| `a24` | film | literary, modern-gothic |
| `aesop` | retail | wabi-sabi, apothecary |
| `apple-film` | film | quiet, cinematic-minimal |
| `arrena` | in-house | bronze-on-black, Mediterranean |
| `boiler-room` | music | raw, nocturnal documentary |
| `cahiers` | film | French editorial, scholarly |
| `criterion` | film | archival, off-white editorial |
| `dazed` | magazine | editorial rebellion, high-contrast |
| `ghibli-museum` | film | hand-made, mythic |
| `letterboxd` | film | friendly-nerd, diary |
| `mubi` | film | disciplined cinephile |
| `opus` | film print | single-serif, quiet |
| `rick-owens` | fashion | brutalist, monastic |
| `synedark` | in-house | luminous, cold, structural |

Add new references as single `.md` files in `references/`. Filename is the slug.

## Design Systems (DESIGN.md library)

A second, complementary library lives in `design_library/`. These are structured `DESIGN.md` files (VoltAgent's `awesome-design-md` format) describing established product brand systems — Linear, Stripe, Apple, Notion, Vercel, Runway. Use these when the deck should adopt the *visual language of a product brand*; use `--aesthetic` when you want a *cinematic mood*.

```bash
# List available design systems
python scripts/design_md.py list

# Inspect what the parser extracts from one
python scripts/design_md.py show linear

# Apply while generating (CLI flag)
python scripts/create_presentation.py \
    --json treatment.json \
    --design-md linear \
    --output out.html

# Or embed in the treatment JSON
{
    "design_md": "stripe",
    "scheme": {"bg": "#000000"},  // overrides Stripe's white canvas
    ...
}
```

**Two formats supported:**

1. **YAML frontmatter** (Linear, Stripe, Apple, Notion) — `colors:` and `typography:` keys map directly into `scheme` and `fonts`.
2. **Prose markdown** (Vercel, Runway) — parser extracts hex codes from `**Name** (#hex): role` lines under `## Color Palette` and font families from `**Primary**: Family` lines under `## Typography`.

**What gets extracted:** `name`, `mood` (one-line voice), `color_palette` (bg/text/text_mid/text_dim/accent/brand), `fonts` (heading/body/mono — proprietary names like `linear display`, `sohne-var`, `geist` are mapped to the closest Google Font, defaulting to Inter), and an `avoid` list pulled from any `## Don't` bullets.

**Adding your own:** Drop a `<slug>.md` (or `<slug>/DESIGN.md`) into `design_library/`. See `design_library/README.md` for the full schema. Run `python scripts/design_md.py show <slug>` to verify what the parser extracts.

`--design-md` and `--aesthetic` can be combined — design_md wins on overlapping keys. Both lose to anything explicitly set in the treatment JSON.

## Output

- **HTML**: Self-contained, GSAP from CDN, images base64-embedded. Open in any browser.
- **PDF**: Playwright/Chromium print mode. All animations disabled, elements forced visible.
- **Video**: ffmpeg x11grab + auto-scroll. 30fps H.264 MP4. Optional background audio. Linux only.
