# Εργατικό Δίκαιο: εξαρτημένη εργασία, αποδοχές, καταγγελία

The labour law depth module. Greek employment law rests on the μίσθωση εργασίας of the
Αστικός Κώδικας (ΑΚ 648 και επόμενα), layered with protective statutes and collective
agreements that the parties cannot contract below. Use this module with
`scripts/vasi_agogis.py` for the two highest frequency labour claim bases (unpaid wages,
dismissal) and with `practice/politiki-dikonomia.md` for the pleading that carries them.

## The gateway question: is it εξαρτημένη εργασία

Almost every protection in this field hinges on one classification: is the relationship a
σύμβαση εξαρτημένης εργασίας (dependent employment) or something else, a σύμβαση
ανεξαρτήτων υπηρεσιών or a σύμβαση έργου. The test is νομική εξάρτηση: whether the worker
provides labour under the direction and control of the employer, integrated into the
employer's organisation, bound as to time, place, and manner of work. The label the parties
put on the contract does not decide it; the real conditions do. A worker dressed as a
freelancer but in fact subordinated is an employee, with all the protections that follow.

Plead the facts of dependence, because the classification is the foundation that the wage,
dismissal, working time, and social security consequences all stand on.

## Αποδοχές: wages and the analytical claim

The employee's core right is the μισθός, owed under the contract or, at the floor, under the
applicable συλλογική σύμβαση εργασίας (ΣΣΕ) or the statutory minimum. Wages are due at the
agreed or customary time, and the employer falls into default from that δήλη ημέρα without
notice (ΑΚ 341), carrying interest (ΑΚ 345). The wage bundle includes more than the basic
salary: overtime (υπερωρία and υπερεργασία), allowances, the holiday bonuses (δώρο
Χριστουγέννων, δώρο Πάσχα), and holiday pay (επίδομα και αποδοχές αδείας).

The δεδουλευμένες αποδοχές claim (`vasi_agogis.py` entry `dedoulevmenes-apodoches`) is the
workhorse, and it is the one most often struck as ποσοτικά αόριστη. A pleading that demands
a lump sum fails. It must break the claim down period by period and head by head, with the
amount of each, so the court can verify each figure. The αοριστία discipline bites hardest
here.

## Χρόνος εργασίας: working time

Working time is heavily regulated: the statutory weekly hours, the rules on υπερεργασία
(the hours just above the contractual week) and υπερωρία (overtime proper, with its premium
and the requirement of lawful authorisation), daily and weekly rest, and annual paid leave.
These have been amended repeatedly, most recently by Ν.4808/2021 and the labour reforms that
followed, including the rules on digital work cards and flexible arrangements. When the
hours or the overtime premium found the claim, fetch the current provision rather than rely
on a remembered figure.

## Καταγγελία: termination and severance

Termination of an indefinite term contract by the employer is the καταγγελία. Historically
Greek law allowed it without stated cause, provided two conditions were met: the written
form, and payment of the statutory αποζημίωση calculated on length of service and earnings
(Ν.2112/1920 for salaried employees, with Ν.3198/1955). A termination missing the form or
the severance is invalid.

Two further controls matter. First, even a formally valid καταγγελία can be void as
καταχρηστική under ΑΚ 281 where it exceeds the bounds of good faith, for instance a
retaliatory or discriminatory dismissal. Second, the framework was reshaped by Ν.4808/2021,
which among other things addressed the validity and the reasons for termination and ratified
ILO Convention 190 on harassment. Because this is live and amended, confirm the current
conditions for a valid dismissal, the severance scale, and any requirement of justification
against the present law before advising.

The dismissal claim (`vasi_agogis.py` entry `apozimiosi-apolysis`) forks at the start, and
the fork must be pleaded cleanly: either the claimant accepts the termination and sues for
unpaid or short severance, or the claimant attacks the termination as void and sues for
recognition of nullity plus μισθοί υπερημερίας (ΑΚ 656), the wages the employer owes for
refusing the offered work. The founding facts differ, so a pleading that blurs the two is
exposed.

## Εργατικό ατύχημα and the collective layer

A work accident gives the injured worker a claim that runs on two tracks: the special
compensation regime of Ν.551/1915 and, for non pecuniary harm, the general αδικοπραξία of
ΑΚ 914 with ηθική βλάβη under ΑΚ 932 where the employer's fault is shown. The social
security dimension (ΕΦΚΑ coverage, benefits, and the interaction with the employer's
liability) sits alongside and should not be conflated with the civil claim. Above the
individual contract sits the collective layer: the ΣΣΕ, which sets floors the individual
contract cannot undercut, and the law of industrial action.

## Honest limits

This module maps the field and gives the two claim bases that dominate labour litigation.
It does not reproduce the severance tables, the exact working time limits, or the current
conditions for a valid dismissal, all of which are statutory figures that have moved with
recent reform and must be read in the present text. Treat the law numbers here as reliable
anchors and the classifications as settled, but fetch the operative figures and confirm any
post 2021 amendment per the citation discipline in `SKILL.md`, and never write a decision
citation you have not verified.
