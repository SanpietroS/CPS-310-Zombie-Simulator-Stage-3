# CPS-310-Zombie-Simulator-Stage-3

Zombie Game Stage 3 — Formal Proposal
Sean Sanpietro
Ha'oh Bode
Specialization — Parallel computing with shared memory

I will study whether sharing one copy of the location data between workers speeds up event processing in the Zombie Simulator.

I will compare serial processing, the current parallel version, and a shared-memory version using the same starting positions and random seeds. I will test populations from 250 to 250,000 agents with two and four workers in the parallel versions. For each setup, I will run ten trials, verify that infections, births, and deaths match across versions, and compare median processing times.
