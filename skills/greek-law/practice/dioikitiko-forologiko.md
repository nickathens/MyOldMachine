# Διοικητικό και Φορολογικό Δίκαιο

The law of the relationship between the citizen and the state, and the tax branch that is
its highest volume application. They are paired because a tax dispute is an administrative
dispute: it runs through the administrative courts on administrative law principles, after
a mandatory administrative stage. Two codes frame the ground: the Κώδικας Διοικητικής
Διαδικασίας (ΚΔΔιαδ, Ν.2690/1999) for how the administration must act, and the Κώδικας
Διοικητικής Δικονομίας (ΚΔΔ, Ν.2717/1999) for litigation before the ordinary
administrative courts.

## The pivot: η εκτελεστή διοικητική πράξη

Administrative review fastens on the εκτελεστή διοικητική πράξη, the act of an authority
that produces legal effects by itself. Identify it first, because the remedy, the court,
and the deadline all follow from it. The state acts under the αρχή της νομιμότητας: every
act needs a legal basis and stays within it.

## The two judicial roads

Greek administrative justice splits by the nature of the dispute:

- Ακυρωτική διαφορά, by αίτηση ακυρώσεως, before the Συμβούλιο της Επικρατείας (or the
  διοικητικά εφετεία where jurisdiction is devolved). The court annuls an unlawful act; it
  does not substitute its own decision. The λόγοι ακυρώσεως are four: αναρμοδιότητα,
  παράβαση ουσιώδους τύπου, παράβαση νόμου, and κατάχρηση εξουσίας. The deadline is sixty
  days from notification or knowledge of the act (ΠΔ 18/1989, confirm).
- Διαφορά ουσίας, by προσφυγή ουσίας, before the τακτικά διοικητικά δικαστήρια (διοικητικά
  πρωτοδικεία and εφετεία). Here the court reviews the merits and may reform the act, not
  only annul it. Tax disputes are διαφορές ουσίας.

Where the law provides an ενδικοφανής προσφυγή (an appeal inside the administration), it
must usually be exhausted before the court is open. Missing that internal step makes the
court action απαράδεκτη.

## Φορολογικό: the tax dispute path

Tax procedure is codified in the Κώδικας Φορολογικής Διαδικασίας. The ΚΦΔ was Ν.4174/2013
for a decade and was recodified by Ν.5104/2024; confirm which numbering governs the act in
hand, since this is recent and exactly the kind of change a consolidation lags on. The
path against an assessment or a fine (πράξη προσδιορισμού φόρου, πράξη επιβολής προστίμου)
is fixed and unforgiving:

1. Ενδικοφανής προσφυγή to the Διεύθυνση Επίλυσης Διαφορών (ΔΕΔ), within thirty days of
   notification of the act. This stage is mandatory; you cannot go straight to court.
2. The ΔΕΔ decides within a statutory period (of the order of one hundred and twenty days,
   confirm). Silence past it counts as σιωπηρή απόρριψη, a tacit rejection that opens the
   next step.
3. Δικαστική προσφυγή before the διοικητικό πρωτοδικείο, within thirty days of the express
   or tacit rejection.

State the contested act, the ground of illegality, and the precise relief. A προσφυγή that
gestures at unfairness without a specific λόγος is rejected on the same logic as a civil
αγωγή struck for αοριστία: the λόγοι must be ορισμένοι, each on its own.

## The αοριστία analog

Administrative pleadings carry the same specificity demand as civil ones, expressed
through their λόγοι rather than a βάση αγωγής. A λόγος ακυρώσεως or a λόγος προσφυγής must
name the rule broken and the way the act breaks it. Generic illegality is not a ground.
The discipline of `practice/politiki-dikonomia.md` transfers: an element pleaded without
its supporting fact does not count.

## Note on the claim basis registry

The βάση αγωγής registry in `scripts/vasi_agogis.py` is for civil claims and does not
extend to administrative remedies, which are built on λόγοι ακυρώσεως or λόγοι προσφυγής
against a specific act. Reason those from this module and the governing codes, with the
same citation care.

## Honest limits

The codes, courts, and deadlines here are well settled, with the genuinely moving pieces
flagged in place (the ΚΦΔ recodification of 2024, the exact deadline figures). Deadlines
in administrative and tax matters are short and strict, and the mandatory ενδικοφανής
stage is the most common way a good case is lost on procedure. Confirm every date and the
current code text before relying on it, and send the user to a δικηγόρος or a λογιστής or
φοροτεχνικός where the matter has a deadline or real exposure.
