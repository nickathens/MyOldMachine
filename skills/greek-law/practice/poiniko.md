# Ποινικό Δίκαιο και Ποινική Δικονομία

Substantive criminal law (Ποινικός Κώδικας, recodified by Ν.4619/2019, in force from 1
July 2019) and criminal procedure (Κώδικας Ποινικής Δικονομίας, Ν.4620/2019). This is
the highest stakes area in the skill and the one where the AI Act line is sharpest, so it
opens with what the skill will not do.

## The refusal, stated first

The skill self classifies as a drafting and research aid, not a high risk system. In
criminal matters that means it refuses, by design:

- predicting whether a specific person is guilty, or what a court will decide.
- predicting or recommending a sentence for a specific case.
- scoring a defendant or a case for risk, or anything dressed as judicial decision
  support.

These are uses the EU AI Act treats as high risk in the administration of justice. The
presumption of innocence (τεκμήριο αθωότητας, ΕΣΔΑ άρθρο 6 παρ. 2, read into the Σύνταγμα)
is not a formality a model may reason around. What the skill does instead: explain the governing
law, set out the structure of liability, draft the procedural documents a party is
entitled to file, and lay the arguments on each side without picking the verdict. When
asked for a forbidden output, say why and offer the legitimate adjacent help.

## The structure of liability (η αξιόποινη πράξη)

Greek doctrine analyses a crime in ordered layers. A defence wins by breaking any one of
them, so the structure is also the checklist:

1. Πράξη and αντικειμενική υπόσταση: a human act or omission that fulfils the objective
   elements of the specific offence in the ΠΚ. For an omission to count, a νομική
   υποχρέωση to act (θέση εγγυητή) must exist.
2. Υποκειμενική υπόσταση: δόλος (ΠΚ 27) as the rule, αμέλεια (ΠΚ 28) only where the
   offence expressly punishes it. The required form of δόλος is read from the offence.
3. Το άδικο: the act is unlawful unless a λόγος άρσης του αδίκου applies, chiefly άμυνα
   (ΠΚ 22) and κατάσταση ανάγκης που αίρει το άδικο (ΠΚ 25, confirm the article).
4. Ο καταλογισμός: blame is attributed only to an offender with ικανότητα προς
   καταλογισμό (ΠΚ 34), absent a ground that excludes it (for instance κατάσταση ανάγκης
   που αίρει τον καταλογισμό, ΠΚ 32, confirm).

Reason offence by offence against the specific article in the special part. Treat the
article numbers above as the well settled general part and confirm the exact text when
it carries the argument.

## Classes of offence

Since the 2019 ΠΚ there are two classes, not three: κακουργήματα (the gravest, tried
before mixed jury or appellate courts) and πλημμελήματα. The old third class, πταίσματα,
was abolished by Ν.4619/2019. The class drives competence, procedure, and παραγραφή, so
fix it early.

## How a case begins and moves

- Initiation: by μήνυση (anyone reporting an offence) or, for offences prosecuted κατ'
  έγκληση, by an έγκληση from the entitled person within the statutory deadline (three
  months from knowledge of the act and the offender, confirm the governing article).
  Offences διωκόμενα αυτεπαγγέλτως need no έγκληση.
- Investigation: προκαταρκτική εξέταση, then ανάκριση (κυρία ανάκριση or προανάκριση)
  where an accused is examined with the rights of the κατηγορούμενος.
- Referral: a δικαστικό συμβούλιο decides by βούλευμα whether to refer to trial or
  acquit (απαλλακτικό βούλευμα), or the prosecutor brings the accused by απευθείας κλήση
  where the law allows.
- Trial and remedies: the ακροατήριο, then ένδικα μέσα, έφεση against the judgment and
  αναίρεση before the Άρειος Πάγος on points of law.

## παραγραφή (extinction by lapse of time)

A core defence to check at once. Under ΠΚ 111 the periods run by class: κακουργήματα the
longest (of the order of fifteen to twenty years), πλημμελήματα five years, with
suspension and interruption rules layered on top. Confirm the exact period and its start
against the current code, because an expired παραγραφή ends the prosecution.

## What the skill drafts

The documents a party is entitled to prepare: μήνυση and έγκληση, the υπόμνημα and
αιτήματα filed during the ανάκριση or before the συμβούλιο, the αίτηση for procedural
steps, and the supporting legal memoranda. Each is drafted in the register of criminal
practice, cites the offence and procedural articles it rests on, and is checked for the
specificity its kind demands. It prepares; it does not file on a lawyer's behalf, and it
does not, ever, pronounce on guilt.

## Note on the claim basis registry

The βάση αγωγής registry in `scripts/vasi_agogis.py` is for civil claims. Criminal
liability is constructed differently, through the layered υπόσταση above, so it is not a
registry entry. The civil αγωγή of a παθών (the αδικοπραξία basis ΑΚ 914, and where a
crime is the unlawful act, the παράσταση πολιτικής αγωγής that joins it to the criminal
case) lives in `practice/enochiko.md`.

## Honest limits

This module is the method and the map, not a verdict. Whether an element is met, whether
a justification applies, and what follows are matters for the δικηγόρος on the evidence,
under the presumption of innocence. A person facing criminal process should see a lawyer
without delay; deadlines here cost liberty, not only money.
