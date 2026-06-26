# Ενοχικό Δίκαιο: οι βάσεις αγωγής και η υπαγωγή

The law of obligations depth module. Its centre is the βάση αγωγής (claim basis): the
rule whose preconditions, once each is matched by a pleaded fact, produce the legal
consequence the plaintiff asks for. Greek private litigation is won or lost on whether
every προϋπόθεση of the invoked rule has a fact behind it. That is the substantive half
of the αοριστία discipline, the half the structural checker cannot judge.

Use this module together with `scripts/vasi_agogis.py`, which holds the elements of the
most common bases as a checklist, and with `practice/politiki-dikonomia.md`, which holds
the structural anatomy of the δικόγραφο that carries the claim.

## How a claim is built (υπαγωγή, applied)

1. Name the βάση αγωγής: the precise rule that grants the remedy (for example ΑΚ 914 for
   tort, ΑΚ 904 for unjust enrichment).
2. List its προϋποθέσεις, element by element. `vasi_agogis.py <slug>` prints them.
3. For each element, point to the πραγματικό περιστατικό in the facts that satisfies it.
   An element with no pleaded fact is ποιοτική αοριστία, and one missing element defeats
   the whole claim.
4. State the έννομη συνέπεια and frame the αίτημα to match it.

This is the test a litigator runs in their head. Making it explicit, element against
fact, is what the skill adds.

## The shape of the field

Ενοχικό δίκαιο is the third book of the Αστικός Κώδικας, in two parts:

- Γενικό μέρος: the life of any obligation regardless of source. Performance and its
  extent (ΑΚ 297 to 300), default of the debtor (υπερημερία οφειλέτη, ΑΚ 340 κ.ε.) and of
  the creditor, impossibility of performance, set off (συμψηφισμός), assignment
  (εκχώρηση), plurality of debtors.
- Ειδικό μέρος: the named sources and contract types. Sale (πώληση), lease (μίσθωση),
  loan (δάνειο), mandate (εντολή), suretyship (εγγύηση), partnership, and the
  non contractual sources: αδικοπραξία (ΑΚ 914 κ.ε.) and αδικαιολόγητος πλουτισμός
  (ΑΚ 904 κ.ε.).

An obligation arises from a δικαιοπραξία (chiefly a σύμβαση) or directly from the law
(tort, unjust enrichment, negotiorum gestio). The source dictates the βάση.

## The core bases

These are the highest frequency claim bases in general practice. The registry in
`vasi_agogis.py` carries the same elements in checklist form.

### Αδικοπραξία, ΑΚ 914

Whoever unlawfully and culpably causes damage to another is bound to make it good. Four
προϋποθέσεις, each needing a fact:

- παράνομη συμπεριφορά: conduct, by act or omission, that breaches a rule of law
  protecting the injured party.
- υπαιτιότητα: δόλος or αμέλεια (ΑΚ 330 defines the standard of care; objective liability
  exists only where a statute creates it).
- ζημία: positive loss or lost profit (ΑΚ 298), quantified.
- αιτιώδης σύνδεσμος: the unlawful and culpable conduct must be the adequate cause of the
  damage (θεωρία της πρόσφορης αιτιότητας).

The recurring αοριστία here is a story that pleads damage and conduct but is silent on
fault or on causation. Both must be pleaded as facts, not assumed.

### Χρηματική ικανοποίηση ηθικής βλάβης, ΑΚ 932

On a completed αδικοπραξία, the court may award a money sum for non pecuniary harm
(ηθική βλάβη), or for ψυχική οδύνη to the family where the act caused death. It is a
separate head that adds to the compensation of pecuniary loss, and the amount is set at
the court's reasoned discretion. Plead the facts that establish the harm, not only the
tort.

### Ενδοσυμβατική ευθύνη από υπερημερία οφειλέτη, ΑΚ 340 with 343

Where a valid, due obligation is not performed on time and the debtor is in default:

- a valid and due (ληξιπρόθεσμη) obligation.
- όχληση of the debtor (ΑΚ 340), unless the day was fixed in advance (δήλη ημέρα, ΑΚ 341),
  in which case default runs without notice.
- the debtor's fault, which is presumed: the debtor carries the burden of proving the
  delay is not attributable to them (ΑΚ 342).
- damage to the creditor from the delay.

The consequence is compensation for the delay damage (ΑΚ 343), and for a money debt,
default interest (τόκοι υπερημερίας, ΑΚ 345). Plead the moment the performance fell due
and the fact of the όχληση or the δήλη ημέρα.

### Αδικαιολόγητος πλουτισμός, ΑΚ 904

Whoever is enriched without lawful cause at another's expense must return the benefit:

- πλουτισμός of the defendant (an increase in assets or an avoided expense).
- at the claimant's expense (from their property or with their loss).
- αιτιώδης συνάφεια between enrichment and loss.
- χωρίς νόμιμη αιτία: a cause that never existed, did not follow, has ceased, or is
  unlawful or immoral.

The claim is subsidiary (επικουρικός χαρακτήρας): it yields where another basis,
contractual or delictual, governs the same shift of wealth. The return is measured by
ΑΚ 908 κ.ε.

## The extent of compensation, ΑΚ 297 to 300

- ΑΚ 297: compensation is paid in money, or, where the creditor asks and it is possible,
  by restoration in kind (αυτούσια αποκατάσταση).
- ΑΚ 298: it covers the reduction of the existing estate (θετική ζημία) and the lost
  profit (διαφυγόν κέρδος) that could be expected in the ordinary course.
- ΑΚ 300: contributory fault (συντρέχον πταίσμα) of the injured party reduces or removes
  the award. It is examined by the court and worth pleading defensively.

A money claim must break the heads of damage down and quantify each, or it risks
ποσοτική αοριστία. The structural checker flags an unquantified money claim; this module
explains why it is fatal.

## Cross cutting limits to check on every claim

- ΑΚ 281, καταχρηστική άσκηση δικαιώματος: a right exercised beyond the bounds of good
  faith, morals, or its social and economic purpose is barred. It is the most invoked
  general clause in Greek private law, as both shield and sword.
- ΑΚ 178 and 179: a δικαιοπραξία contrary to morals (ιδίως υπέρμετρη δέσμευση) is void.
- Παραγραφή: the general limitation is twenty years (ΑΚ 249), but many claims run shorter.
  A tort claim is time barred five years after the injured party learned of the damage and
  the liable person, and in any case twenty years from the act (ΑΚ 937). Confirm the
  applicable period and its start against the current code before relying on it; an expired
  παραγραφή is a complete defence.

## Honest limits

This module gives the elements and the method. It does not decide your case. Whether a
given fact satisfies an element, whether a general clause applies, and whether a defence
bites are matters of υπαγωγή against the governing rule and the νομολογία on it. Treat the
article numbers and elements here as well settled doctrine, but when the exact statutory
wording carries the argument, fetch it (e-nomothesia.gr or the gazette) per the citation
discipline in SKILL.md, and never write a decision citation you have not verified.
