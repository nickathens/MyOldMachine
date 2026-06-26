# Εμπράγματο Δίκαιο: κυριότητα, νομή και οι αγωγές προστασίας

The property law depth module. Real rights bind everyone (έναντι πάντων), unlike the
relative bond of an ενοχή, and they are a closed list: parties cannot invent a new real
right (numerus clausus, αρχή του κλειστού αριθμού). The litigation that follows turns on
two questions the pleading must answer cleanly: who holds the right, and what is the exact
thing. Both are frequent sources of αοριστία, so this module pairs with
`scripts/vasi_agogis.py` (the property bases) and `practice/politiki-dikonomia.md`.

## Three relations to a thing

Greek law separates three things English often blurs:

- κυριότητα (ownership): the full real right over a thing, the legal title.
- νομή (possession): physical control held with the intent of an owner (διάνοια κυρίου).
  Possession is protected in its own right, independently of ownership.
- κατοχή (detention): physical control held for another, without owner's intent (the
  tenant, the borrower, the depositary).

The distinction is load bearing. A νομέας who is not the owner can still defend their
possession (αγωγές νομής), and an owner who has lost possession sues to recover the thing
(διεκδικητική). Plead which relation the client holds, and prove it.

## Acquiring ownership

### Derivative acquisition (παράγωγη κτήση)

Transfer from the previous owner, by agreement plus a formal act:

- Immovables (ΑΚ 1033): agreement before a notary that title passes for a lawful cause
  (συμβολαιογραφικό έγγραφο), followed by μεταγραφή in the public books (or registration in
  the Κτηματολόγιο where it is operative). Without the transcription the transfer is not
  complete.
- Movables (ΑΚ 1034): delivery of possession (παράδοση της νομής) by the owner, plus the
  agreement of both that ownership passes.

A buyer takes only the right the seller had (nemo plus iuris), subject to the good faith
acquisition exceptions the code provides for movables.

### Original acquisition (πρωτότυπη κτήση)

Ownership arises afresh, not derived from a predecessor. The litigation workhorse is
χρησικτησία (usucapion):

- Τακτική χρησικτησία (ΑΚ 1041): possession with good faith (καλή πίστη) and a legal title
  (νόμιμος τίτλος) for ten years as to immovables (the period is shorter for movables;
  confirm it against the current article).
- Έκτακτη χρησικτησία (ΑΚ 1045): possession of the thing for twenty years, with no need of
  title or good faith.

χρησικτησία is pleaded as a chain of facts: the start of possession, its character
(διάνοια κυρίου), and its continuity for the whole period. It is also the usual way an
owner proves title in a διεκδικητική when the paper chain is imperfect.

## The property claims (βάσεις)

`vasi_agogis.py` carries the elements. In brief:

- Διεκδικητική αγωγή (ΑΚ 1094): the owner who has lost possession recovers the thing from
  whoever possesses or detains it. The plaintiff pleads ownership and how it was acquired,
  the defendant's possession or detention, and the precise identity of the thing.
- Αρνητική αγωγή (ΑΚ 1108): the owner who is disturbed in a way short of dispossession
  obtains removal of the disturbance and its cessation for the future.
- Αγωγές προστασίας της νομής (ΑΚ 987 for αποβολή, ΑΚ 989 for διατάραξη): the possessor,
  owner or not, is restored or protected against an unlawful interference with possession.
  These run on a short forfeiture deadline of roughly one year from the interference;
  confirm the exact article (ΑΚ 992) and date, because it is unforgiving.

The διεκδικητική carries the heaviest αοριστία exposure of the three. An immovable must be
described by location, boundaries and area precisely enough that no identity doubt remains,
and the acquisition chain must be set out, not asserted.

## Real security (εμπράγματη ασφάλεια), in brief

Real rights that secure a claim against a specific thing, surviving its transfer:

- Υποθήκη: a mortgage over an immovable, created by a title and its registration
  (εγγραφή) in the public books. It gives the creditor priority and the right to satisfy
  the debt from the thing.
- Προσημείωση υποθήκης: a conditional pre notation ordered by a court, that converts to a
  full υποθήκη on a final judgment, ranking from the date it was entered.
- Ενέχυρο: a pledge over a movable, classically created by delivery of the thing to the
  creditor.

Treat these functionally here and fetch the governing articles and their exact conditions
from the code before drafting or advising, since the registration and ranking rules are
detailed and decisive.

## Κτηματολόγιο

The Εθνικό Κτηματολόγιο (Ν.2664/1998) is replacing the old transcription system
(σύστημα μεταγραφών) area by area. Where it is operative, the act of publicity is
registration in the cadastre rather than μεταγραφή, the entries are organised by parcel
(ΚΑΕΚ) rather than by person, and a first registration that goes unchallenged within the
statutory window can harden into title. For any immovable matter, establish first which
regime governs the parcel, because it changes how ownership is proved and how a transfer
is completed.

## Honest limits

The article numbers and the elements here are well settled doctrine, but property law is
detail heavy: prescription periods, the good faith acquisition exceptions, the cadastre
transition rules, and the security ranking rules all turn on exact statutory text. Treat
this module as the map, confirm the operative article when its wording carries the
argument (per the citation discipline in SKILL.md), and never assert a cadastral or
registry fact about a specific parcel without the actual record in hand.
