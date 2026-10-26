# Metadata retrieval evaluation inputs

`selection.json` fixes 120 genuine upstream identities, 40 from each pinned
SimReady, Scenario and ChemLab dataset. It records metadata digests and counting
units; it does not bundle publisher catalogs, previews or models. Source and
asset licensing remain distinct from platform acquisition admission.

`queries.json` contains 40 independently assigned English/Chinese queries.
Implementers received only the 16 tuning queries. The 24 retained queries
(12 per language, including ten positive and two zero/unknown cases) stayed
sealed until the implementation repairs froze. The user confirmed the sampled
use intents on 2026-10-26 without changing their labels. The original label-file
digest is retained; query answers must not be used to tune retrieval.

Build the actual product metadata at these revisions, freeze its exact records,
and run the evaluator with this selection and query file. Missing identities
fail explicitly. No names, descriptions, tags or relevance values are fabricated
by the evaluator. Deterministic fixtures and real CPU models are reported as
different evidence. Unknown technical facts never satisfy hard conditions.

For a 1000-record performance run, retain the representative identities and add
other genuine product records in a stable order. Report the input digest and
actual record/model/index versions; do not replicate records to reach the count.
Model loading, index construction and loaded query latency have separate timers.
Real Agent organization quality remains unverified until the configured runtime
and its usage budget are explicitly selected.
