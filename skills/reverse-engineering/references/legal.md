# Legal and ethics gate

Reverse engineering is lawful for a defined set of purposes and unlawful for others.
This skill serves the lawful ones and refuses the rest. The gate is not decoration:
the dossier schema only lets you record a lawful `purpose`, and the analyst states the
concrete basis in one sentence before work starts. If the honest basis is not on the
allowed list, stop.

This file is practical guidance, not legal advice. For a real legal question (a client
contract, a cease and desist, a jurisdiction call), use the `greek-law` skill or a
lawyer. If you operate from the EU, EU law is primary; US law (DMCA) matters when
the target or its publisher is US based. Adjust for your own jurisdiction.

## Allowed purposes (the dossier `lawful_basis.purpose` enum)

1. **interoperability.** Understanding how a program exchanges data or calls so you can
   build something that works with it. In the EU this is a protected act under the
   Software Directive (below). This is the strongest footing RE has.
2. **security_research.** Finding, understanding, or verifying vulnerabilities, for
   defence or for an authorized disclosure. The `security-audit` skill is the sibling for
   source audits.
3. **format_understanding.** Reading an undocumented file or wire format so data can be
   recovered, converted, or validated. Includes your own files trapped in a dead tool.
4. **own_work.** A target you own, wrote, or are contracted to work on. A client site, a
   plugin you licensed and must debug, your own compiled build.
5. **education.** Learning how a technique works, including studying a public reference
   to inform a comparable build of your own, as long as it stays observation and no code
   or asset is copied.

## EU: Directive 2009/24/EC, Article 6 (Decompilation)

A lawful user may decompile a program without the rightholder's permission only when all
three conditions hold together:

1. the act is done by a licensee or a person with a right to use a copy, or on their
   behalf by an authorized person;
2. the information needed for interoperability was not previously readily available; and
3. the acts are confined to the parts of the program necessary to achieve
   interoperability.

Article 6(2) then forbids: using what you learn for any goal other than interoperability;
passing it to others except where interoperability requires it; and using it to build a
substantially similar program. Article 6(3) adds that none of this may unreasonably
prejudice the rightholder's legitimate interests or conflict with normal exploitation of
the program.

Note from case law: the CJEU in Case C-13/20 (Top System v Selor, 2021) held that
decompiling to correct errors is not the Article 6 interoperability act; a lawful user's
right to fix errors sits under Article 5(1) instead. So "I am debugging a program I am
licensed to use" and "I am building an interoperable program" are two different legal
footings. Name the right one.

Source: Directive 2009/24/EC Article 6, via
https://www.legislation.gov.uk/eudr/2009/24/article/6 and CJEU C-13/20.

## US: 17 U.S.C. Section 1201(f) (Reverse Engineering)

A person who has lawfully obtained the right to use a copy of a program may circumvent an
access control to identify and analyze the elements necessary for interoperability, to
the extent those acts do not themselves infringe. Subsection (f)(2) permits developing
the tools necessary to perform that circumvention.

Courts read this narrowly. In the DeCSS litigation the exemption was held to cover
computer programs, not technological systems that protect other works such as films, and
the "sole purpose of interoperability" requirement failed because the tool also served
other uses. Treat Section 1201(f) as a tight interoperability lane, not a general licence.

Source (secondary, verify against 17 U.S.C. 1201(f) directly for any real matter):
https://cs.stanford.edu/people/eroberts/cs201/projects/1999-00/dvd-css/lawsuits.htm and
the Copyright Office 1201 materials at https://www.copyright.gov/1201/.

## Refused, every time

- Circumventing DRM or any technological protection measure to access or copy a work
  (films, music, books, paid assets). The EU interoperability act and 1201(f) are about
  programs and interoperability, not unlocking content.
- Defeating copy protection, licensing, activation, or online entitlement checks so a
  paid thing runs unpaid.
- Extracting copyrighted assets (models, textures, audio, fonts) to redistribute or ship
  in another product.
- Bypassing anti-cheat or integrity systems to gain an advantage in a live multiplayer
  game, or building cheats.
- Anything whose honest one sentence basis is "to avoid paying for it" or "to pirate it".

If a request lands here, say so plainly and stop. Being the tool that refuses this is what
keeps the tool trustworthy for the work that is allowed.

## Recording the basis

In every dossier set:

```json
"lawful_basis": { "purpose": "interoperability", "note": "<one concrete sentence>" }
```

The note names the real thing: which client, which program you licensed, which format you
need to read, which public reference you are studying and that nothing is copied. The
validator enforces the enum; you enforce the honesty of the note.
