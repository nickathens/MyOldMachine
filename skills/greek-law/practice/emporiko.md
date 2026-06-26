# Εμπορικό και Εταιρικό Δίκαιο: έμπορος, αξιόγραφα, εταιρείες, αφερεγγυότητα

The commercial and company law depth module. Greek commercial law sits on top of the
Αστικός Κώδικας: the general rules of obligations and property in `practice/enochiko.md`
and `practice/empragmato.md` still govern, and commercial law adds special rules where
the activity is εμπορική. Use this module with `scripts/vasi_agogis.py` for the
commercial claim bases (the dishonored cheque, board liability) and with
`practice/politiki-dikonomia.md` for the δικόγραφο that carries them.

## Who is an έμπορος, and what is an εμπορική πράξη

Greek law keeps two parallel systems for deciding when commercial rules apply.

- The objective system: certain acts are commercial by their nature (εμπορικές πράξεις),
  listed in the old Εμπορικός Νόμος and the Β.Δ. of 1835 that still governs the
  definition. Whoever performs them is touched by commercial law for that act.
- The subjective system: a person who carries out commercial acts as their profession is
  an έμπορος, and a presumption then treats their further acts as commercial.

The classification matters because it pulls in special consequences: commercial custom,
the εμπορική παραγραφή where it applies, joint and several liability of co debtors as a
default (against the divided liability the ΑΚ presumes in civil matters), the bankruptcy
regime, and the jurisdiction of the τμήματα that hear commercial disputes. When the
commercial character of an act founds the claim, plead the facts that establish it.

## Αξιόγραφα: the abstract obligation

A αξιόγραφο (negotiable instrument) embeds a right in a document, so that the right
travels with the paper and the holder sues on the instrument itself, largely cut off from
the underlying cause (the αναιτιώδης or abstract nature of the obligation). The three core
instruments and their governing statutes:

- συναλλαγματική (bill of exchange) and γραμμάτιο εις διαταγήν (promissory note):
  Ν.5325/1932.
- επιταγή (cheque): Ν.5960/1933.

The practical engine here is the ακάλυπτη επιταγή, the dishonored cheque, which is one of
the most litigated commercial claims. The holder who presents a cheque in time and is met
with a bank stamp of no funds has a recourse claim (αξίωση εξ αναγωγής) on the instrument,
and, by settled Άρειος Πάγος νομολογία, a parallel αδικοπραξία claim under ΑΚ 914, since
issuing a cheque without cover is treated as an unlawful and culpable act that can also
carry ηθική βλάβη (ΑΚ 932). The penal side (Ν.5960/1933 άρθρο 79) is separate from the
civil claim and is not a precondition of it. The elements are in `vasi_agogis.py` under
`epitagi-akalypti`. Plead the instrument's particulars, the timely presentation, and the
dishonor stamp, and confirm the presentation deadline against the current article before
relying on it.

## The company forms and their governing laws

Greek company law was recodified over the last decade, so the governing statute depends on
the form. Hold these straight:

- Ομόρρυθμη εταιρεία (ΟΕ) and ετερόρρυθμη εταιρεία (ΕΕ): Ν.4072/2012. Personal companies.
  In the ΟΕ every partner has unlimited, joint and several liability for the company debts;
  in the ΕΕ the ετερόρρυθμος partner is liable only up to their contribution.
- Ιδιωτική κεφαλαιουχική εταιρεία (ΙΚΕ): Ν.4072/2012. A flexible capital company, low
  minimum capital, liability limited to the company except for any guarantee contributions.
- Εταιρεία περιορισμένης ευθύνης (ΕΠΕ): Ν.3190/1955. The older limited liability form, now
  largely displaced by the ΙΚΕ in practice.
- Ανώνυμη εταιρεία (ΑΕ): Ν.4548/2018, which replaced the long standing κ.ν. 2190/1920. The
  capital company for larger undertakings, managed by a διοικητικό συμβούλιο.

The dividing line that decides most disputes is between the προσωπικές εταιρείες (where
partners can be reached personally) and the κεφαλαιουχικές εταιρείες (where the veil
normally limits creditors to the company assets). Piercing that veil is exceptional and
needs its own pleaded basis (abuse, commingling, undercapitalization), not a bare
assertion that the company cannot pay.

## Liability of management

In the ΑΕ the members of the διοικητικό συμβούλιο owe the company a duty of care, measured
against the διαχείριση of a prudent businessperson, and are liable for damage caused by a
breach that falls outside the protected zone of honest business judgment. The claim
belongs to the company. The governing provisions are in Ν.4548/2018 (confirm the exact
articles, around 102 to 104, and the conditions for bringing the action, including minority
shareholder standing). The registry entry `efthyni-ds` carries the elements. The recurring
αοριστία is a general accusation of mismanagement: a sound pleading isolates the specific
act or omission and ties it causally to a quantified loss.

For the ΕΠΕ the διαχειριστής, and for personal companies the managing partners, carry
analogous duties under their own statutes. Identify the form first, because the liability
rule and the article follow from it.

## Αφερεγγυότητα: the insolvency regime

Insolvency is now consolidated in the Κώδικας Ν.4738/2020 (ρύθμιση οφειλών και παροχή
δεύτερης ευκαιρίας), which replaced the older Πτωχευτικός Κώδικας Ν.3588/2007 and folded in
the out of court workout and the second chance discharge for natural persons. When a matter
turns on πτώχευση, the order of creditors, or a debt restructuring, reason from Ν.4738/2020
and fetch the current text, because this area has been amended repeatedly since 2020.

## Honest limits

This module orients you across the commercial field and gives the two highest frequency
claim bases in full. It is not a substitute for the specialised statutes: company
formation, capital and governance rules, the detail of the αξιόγραφα defences, and the
insolvency procedure each have dense provisions that must be read in the current text.
Treat the law numbers here as reliable anchors, but confirm specific article numbers,
deadlines, and any post 2020 amendment against the gazette or a consolidated source per the
citation discipline in `SKILL.md`, and never write a decision citation you have not
verified.
