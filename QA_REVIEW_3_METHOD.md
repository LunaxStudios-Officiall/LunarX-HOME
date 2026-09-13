# QA Review 3 — frozen artifact method

The final ZIP is hashed before review, treated as immutable, safety-checked, extracted into a new empty directory, then validated/tested from that extraction. The archive is rejected if any source change is required. Because embedding the post-freeze QA3 result would mutate the frozen archive, the final QA3 decision and final ZIP SHA-256 are reported alongside the delivered artifact rather than written back into it.
